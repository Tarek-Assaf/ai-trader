from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from random import Random

import pytest
from sqlalchemy import create_engine

from trader.agents.llm.client import LLMClient
from trader.agents.llm.ledger import SqlUsageLedger
from trader.agents.llm.providers import MockLLM
from trader.agents.llm.types import ProviderReply
from trader.core.config import load_config_file
from trader.data.resample import Candle
from trader.lab.ledger import ExperimentLedger
from trader.lab.miner import PatternMiner
from trader.lab.pipeline import IdeaGenerator, ValidationPipeline
from trader.lab.promotion import PromotionGate
from trader.lab.spec import IndicatorCondition, StopExit, StrategySpec, TargetExit
from trader.lab.validator import Gate1Config, HoldoutReader, ValidationResult


def _bars(count: int = 120, *, start_minute: int = 0) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=start_minute)
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


def _trend(count: int, *, start_minute: int = 0, slope: float = 0.3) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=start_minute)
    return [
        Candle(
            start + timedelta(minutes=index),
            100 + index * slope,
            100.4 + index * slope,
            99.9 + index * slope,
            100.3 + index * slope,
            1,
            ("NAS100",),
        )
        for index in range(count)
    ]


def _permissive_gate_config() -> Gate1Config:
    return Gate1Config(
        min_trades=0,
        min_out_of_sample_trades=0,
        min_walk_forward_windows=0,
        min_validation_periods=0,
        min_out_of_sample_profit_factor=0,
        min_holdout_profit_factor=0,
        min_profitable_walk_forward_share=0,
        cost_stress_increase_pct=50,
        require_neighbor_parameters_profitable=False,
        require_multiple_testing_correction=False,
        min_profitable_regimes=0,
        max_top5pct_trades_profit_share=1,
        max_backtest_drawdown_pct=1_000_000,
    )


def _noise(count: int, *, seed: int, start_minute: int = 0) -> list[Candle]:
    rng = Random(seed)
    start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=start_minute)
    close = 100.0
    candles: list[Candle] = []
    for index in range(count):
        close += rng.uniform(-0.8, 0.8)
        spread = rng.uniform(0.05, 0.45)
        candles.append(
            Candle(
                start + timedelta(minutes=index),
                close + rng.uniform(-spread * 0.5, spread * 0.5),
                close + spread,
                close - spread,
                close,
                1,
                ("NAS100",),
            )
        )
    return candles


def test_pipeline_reads_holdout_once_and_calls_validator_after_metrics(tmp_path: Path) -> None:
    candles = _trend(120)
    out_of_sample = _trend(30, start_minute=len(candles))
    ledger = ExperimentLedger()
    candidate = StrategySpec(
        schema_version=1,
        instrument="NAS100",
        direction="long",
        timeframe_minutes=1,
        entry_conditions=(IndicatorCondition(indicator="close", operator="gt", threshold=0.0),),
        exit_rules=(StopExit(kind="stop", distance=1.0), TargetExit(kind="target", distance=0.2)),
    )
    holdout_path = tmp_path / "sealed.json"
    holdout_path.write_bytes(b"holdout")
    calls: list[dict[str, object]] = []

    def reject(metrics: Mapping[str, object], *, holdout_spec_hash: str) -> ValidationResult:
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

    config = _permissive_gate_config()
    result = ValidationPipeline(ledger=ledger, config=config).run(
        [candidate],
        candles,
        holdout_reader=HoldoutReader(tmp_path, ledger=ledger),
        holdout_path=holdout_path,
        holdout_loader=lambda raw: candles[:30],
        out_of_sample=out_of_sample,
        train_bars=60,
        test_bars=20,
        validator=reject,
    )

    assert len(result) == 1
    assert not result[0].promoted
    assert calls and "out_of_sample_profit_factor" in calls[0]
    assert sum(item.holdout_accessed for item in ledger.records()) == 1


def test_pipeline_repeated_spec_hash_fails_closed_on_second_holdout_read(tmp_path: Path) -> None:
    candles = _trend(120)
    out_of_sample = _trend(30, start_minute=len(candles))
    candidate = StrategySpec(
        schema_version=1,
        instrument="NAS100",
        direction="long",
        timeframe_minutes=1,
        entry_conditions=(IndicatorCondition(indicator="close", operator="gt", threshold=0.0),),
        exit_rules=(StopExit(kind="stop", distance=1.0), TargetExit(kind="target", distance=0.2)),
    )
    ledger = ExperimentLedger()
    sealed = tmp_path / "sealed.bin"
    sealed.write_bytes(b"sealed")
    decisions = ValidationPipeline(ledger=ledger, config=_permissive_gate_config()).run(
        [candidate, candidate],
        candles,
        holdout_reader=HoldoutReader(tmp_path, ledger=ledger),
        holdout_path=sealed,
        holdout_loader=lambda raw: out_of_sample,
        out_of_sample=out_of_sample,
    )

    assert len(decisions) == 2
    assert decisions[0].validation.passed
    assert not decisions[1].validation.passed
    assert decisions[1].validation.reasons == ("holdout_read_failed",)
    assert sum(item.holdout_accessed for item in ledger.records()) == 1


def test_real_config_noise_never_promotes_across_random_variants(tmp_path: Path) -> None:
    """Acceptance fixture: random walks cannot pass the real Strategy Lab gates."""
    for seed in range(3):
        in_sample = _noise(1_000, seed=seed)
        out_of_sample = _noise(300, seed=seed + 100, start_minute=len(in_sample))
        holdout = _noise(300, seed=seed + 200, start_minute=len(in_sample) + len(out_of_sample))
        candidates = [
            StrategySpec(
                schema_version=1,
                instrument="NAS100",
                direction="long" if variant % 2 == 0 else "short",
                timeframe_minutes=1,
                entry_conditions=(
                    IndicatorCondition(
                        indicator="close",
                        operator="gt" if variant % 2 == 0 else "lt",
                        threshold=float(99 + variant / 10),
                    ),
                ),
                exit_rules=(
                    StopExit(kind="stop", distance=1.0),
                    TargetExit(kind="target", distance=0.8),
                ),
            )
            for variant in range(200)
        ]
        ledger = ExperimentLedger()
        sealed = tmp_path / f"sealed-{seed}.bin"
        sealed.write_bytes(b"sealed noise fixture")
        decisions = ValidationPipeline(ledger=ledger).run(
            candidates,
            in_sample,
            holdout_reader=HoldoutReader(tmp_path, ledger=ledger),
            holdout_path=sealed,
            holdout_loader=lambda raw, bars=holdout: bars,
            out_of_sample=out_of_sample,
        )

        assert len(decisions) == 200
        assert all(not decision.promoted for decision in decisions)


def test_pipeline_rejects_overlapping_out_of_sample(tmp_path: Path) -> None:
    candles = _bars()
    candidate = PatternMiner(ledger=ExperimentLedger()).scan(candles).candidates[0]
    with pytest.raises(ValueError, match="overlaps"):
        ValidationPipeline().run(
            [candidate],
            candles,
            holdout_reader=HoldoutReader(tmp_path, ledger=ExperimentLedger()),
            holdout_path=tmp_path / "missing",
            holdout_loader=lambda raw: (),
            out_of_sample=candles[-10:],
        )


def test_pipeline_does_not_read_holdout_for_cheap_gate_failure(tmp_path: Path) -> None:
    candles = _bars()
    out_of_sample = _bars(30, start_minute=len(candles))
    candidate = PatternMiner(ledger=ExperimentLedger()).scan(candles).candidates[0]
    config = Gate1Config(
        min_trades=10_000,
        min_out_of_sample_trades=0,
        min_walk_forward_windows=0,
        min_validation_periods=0,
        min_out_of_sample_profit_factor=0,
        min_holdout_profit_factor=0,
        min_profitable_walk_forward_share=0,
        cost_stress_increase_pct=50,
        require_neighbor_parameters_profitable=False,
        require_multiple_testing_correction=False,
        min_profitable_regimes=0,
        max_top5pct_trades_profit_share=1,
        max_backtest_drawdown_pct=1_000_000,
    )

    class Reader:
        reads = 0

        def read(self, spec_hash: str, path: str | Path) -> bytes:
            self.reads += 1
            return b"unreachable"

    reader = Reader()
    result = ValidationPipeline(config=config).run(
        [candidate],
        candles,
        holdout_reader=reader,
        holdout_path=tmp_path / "sealed",
        holdout_loader=lambda raw: (),
        out_of_sample=out_of_sample,
    )
    assert reader.reads == 0
    assert result[0].validation.holdout_read is False


@pytest.mark.asyncio
async def test_invalid_idea_is_discarded_and_counted() -> None:
    from trader.agents.llm.types import InvalidModelOutput

    class BadLLM:
        async def complete(self, *args: object, **kwargs: object) -> object:
            raise InvalidModelOutput("invalid")

    ledger = ExperimentLedger()
    generator = IdeaGenerator(BadLLM(), ledger=ledger)
    assert await generator.generate(instrument="NAS100", summary={}) is None
    assert generator.rejected_outputs == 1
    assert ledger.records()[0].metrics["rejected_outputs"] == 1


def test_pipeline_real_config_promotes_planted_pattern_and_emits_alert(tmp_path: Path) -> None:
    def trend(count: int, start_minute: int) -> list[Candle]:
        start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=start_minute)
        return [
            Candle(
                start + timedelta(minutes=index),
                100 + index * 0.3,
                100.4 + index * 0.3,
                99.9 + index * 0.3,
                100.3 + index * 0.3,
                1,
                ("NAS100",),
            )
            for index in range(count)
        ]

    spec = StrategySpec(
        schema_version=1,
        instrument="NAS100",
        direction="long",
        timeframe_minutes=1,
        entry_conditions=(IndicatorCondition(indicator="close", operator="gt", threshold=0.0),),
        exit_rules=(
            StopExit(kind="stop", distance=1.0),
            TargetExit(kind="target", distance=0.2),
        ),
    )
    in_sample = trend(1_000, 0)
    out_of_sample = trend(300, len(in_sample))
    holdout = trend(300, len(in_sample) + len(out_of_sample))
    sealed = tmp_path / "sealed.bin"
    sealed.write_bytes(b"sealed fixture")
    ledger = ExperimentLedger()
    events: list[object] = []
    alerts: list[str] = []
    config = load_config_file(Path("config/strategy_lab.yaml")).model_dump()
    result = ValidationPipeline(ledger=ledger).run(
        [spec],
        in_sample,
        holdout_reader=HoldoutReader(tmp_path, ledger=ledger),
        holdout_path=sealed,
        holdout_loader=lambda raw: holdout,
        out_of_sample=out_of_sample,
        train_bars=100,
        test_bars=100,
        promotion_gate=PromotionGate(config, audit=events, alert=alerts.append),
    )
    assert result[0].validation.passed
    assert result[0].promotion is not None
    assert result[0].promotion.stage == "paper"
    assert events and alerts == [f"strategy_stage_changed:{result[0].strategy_hash}:paper"]
    assert sum(record.holdout_accessed for record in ledger.records()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "```python\nopen('config/risk.yaml', 'w').write('risk: 999')\n```",
        '{"gate1_backtest_to_paper": {"min_trades": 0}}',
        '{"holdout_request": "read sealed fixture"}',
    ],
)
async def test_mock_llm_untrusted_lab_requests_are_rejected_without_holdout_access(
    tmp_path: Path, payload: str
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    provider = MockLLM([ProviderReply(payload, 1, 1), ProviderReply(payload, 1, 1)])
    client = LLMClient(
        config={
            "tiers": {
                "strong": {
                    "provider": "mock",
                    "model": "mock",
                    "max_input_tokens": 5_000,
                    "max_output_tokens": 1_000,
                    "input_usd_per_million": 1.0,
                    "output_usd_per_million": 1.0,
                },
                "mid": {
                    "provider": "mock",
                    "model": "mock",
                    "max_input_tokens": 5_000,
                    "max_output_tokens": 1_000,
                    "input_usd_per_million": 1.0,
                    "output_usd_per_million": 1.0,
                },
            },
            "roles": {"strategist": "strong"},
            "fallback": {"strong": "mid"},
            "health_probe": {
                "interval_minutes": 15,
                "timeout_seconds": 1,
                "failures_to_open": 3,
                "cooldown_minutes": 5,
            },
        },
        providers={"mock": provider},
        ledger=SqlUsageLedger(engine, daily_cap_usd=Decimal("1"), monthly_cap_usd=Decimal("1")),
    )
    ledger = ExperimentLedger()

    class CountingReader:
        reads = 0

        def read(self, spec_hash: str, path: str | Path) -> bytes:
            self.reads += 1
            raise AssertionError("untrusted LLM output must never reach holdout access")

    reader = CountingReader()
    before = Path("config/strategy_lab.yaml").read_bytes()
    rejected = IdeaGenerator(client, ledger=ledger)

    assert await rejected.generate(instrument="NAS100", summary={}) is None
    assert rejected.rejected_outputs == 1
    assert provider.calls == 2
    assert Path("config/strategy_lab.yaml").read_bytes() == before
    assert not [item for item in ledger.records() if item.holdout_accessed]
    assert reader.reads == 0
