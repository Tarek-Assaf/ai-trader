from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.ledger import ExperimentLedger
from trader.lab.miner import PatternMiner
from trader.lab.pipeline import ValidationPipeline
from trader.lab.validator import HoldoutReader, ValidationResult


def _bars(count: int = 120) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            start + timedelta(minutes=index),
            100 + index * 0.1,
            101 + index * 0.1,
            99 + index * 0.1,
            100 + index * 0.1,
            1,
            ("NAS100",),
        )
        for index in range(count)
    ]


def test_pipeline_reads_holdout_once_and_calls_validator_after_metrics(tmp_path) -> None:
    candles = _bars()
    ledger = ExperimentLedger()
    candidate = PatternMiner(ledger=ledger).scan(candles).candidates[0]
    holdout_path = tmp_path / "sealed.json"
    holdout_path.write_bytes(b"holdout")
    calls: list[dict[str, object]] = []

    def reject(metrics, *, holdout_spec_hash: str) -> ValidationResult:
        calls.append(dict(metrics))
        return ValidationResult(
            False,
            ("test_reject",),
            0.0,
            ledger.count(),
            holdout_spec_hash,
            True,
            metrics,
        )

    result = ValidationPipeline(ledger=ledger).run(
        [candidate],
        candles,
        holdout_reader=HoldoutReader(tmp_path, ledger=ledger),
        holdout_path=holdout_path,
        holdout_loader=lambda raw: candles[:30],
        validator=reject,
    )

    assert len(result) == 1
    assert not result[0].promoted
    assert calls and "out_of_sample_profit_factor" in calls[0]
    assert sum(item.holdout_accessed for item in ledger.records()) == 1
