from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.ledger import ExperimentLedger
from trader.lab.miner import PATTERN_NAMES, PatternMiner, mine_patterns


def _candles(count: int = 80) -> list[Candle]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        Candle(
            ts_utc=start + timedelta(minutes=index),
            open=100.0 + index * 0.05,
            high=100.2 + index * 0.05,
            low=99.8 + index * 0.05,
            close=100.1 + index * 0.05,
            volume=10.0,
            series_key=("NSXUSD",),
        )
        for index in range(count)
    ]


def test_miner_emits_all_pattern_families_and_records_every_hypothesis() -> None:
    ledger = ExperimentLedger()
    result = PatternMiner(ledger=ledger).scan(_candles(), validator=lambda metrics: False)

    assert {candidate.name for candidate in result.candidates} == set(PATTERN_NAMES)
    assert result.hypotheses_tested == 10
    assert len(result.candidates) == 10
    assert len(ledger.records()) == result.hypotheses_tested
    assert result.promoted == ()
    assert all(candidate.strategy.instrument == "NSXUSD" for candidate in result.candidates)


def test_miner_uses_validator_only_after_ledger_append() -> None:
    ledger = ExperimentLedger()
    seen_counts: list[int] = []

    def validator(metrics: dict[str, object]) -> bool:
        seen_counts.append(ledger.count())
        return metrics["pattern"] == "mean_reversion"

    result = mine_patterns(_candles(), ledger=ledger, validator=validator)

    assert seen_counts == list(range(1, 11))
    assert {candidate.name for candidate in result.promoted} == {"mean_reversion"}


def test_miner_is_safe_for_empty_input_when_instrument_is_explicit() -> None:
    result = PatternMiner(instrument="XAUUSD").scan(())

    assert len(result.candidates) == 10
    assert all(item.strategy.instrument == "XAUUSD" for item in result.candidates)

