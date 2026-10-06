import random
from datetime import UTC, datetime, timedelta

from trader.lab.backtest import CostModel, run_backtest
from trader.lab.spec import ComplexityLimits, parse_strategy_spec
from trader.lab.validator import Gate1Config, validate_gate1

from trader.data.resample import Candle
from trader.lab.ledger import ExperimentLedger, ExperimentRecord
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


def test_derived_metrics_noise_variants_never_promote() -> None:
    """The ledger count, rather than hand-written metrics, drives Gate 1."""
    limits = ComplexityLimits(max_conditions_per_strategy=5, max_tunable_parameters=4)
    config = Gate1Config(1, 1, 1, 1, 1.0, 1.0, 0.5, 50, True, True, 1, 0.9, 20)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for seed in (7, 19, 31):
        randomizer = random.Random(seed)
        price = 100.0
        candles: list[Candle] = []
        for index in range(40):
            price += randomizer.uniform(-1.0, 1.0)
            candles.append(
                Candle(
                    start + timedelta(minutes=index),
                    price,
                    price + 1,
                    price - 1,
                    price,
                    1,
                    ("NAS100",),
                )
            )
        ledger = ExperimentLedger()
        promotions = 0
        for variant in range(2_000):
            threshold = 95.0 + randomizer.random() * 10.0
            spec = parse_strategy_spec(
                {
                    "schema_version": 1,
                    "instrument": "NAS100",
                    "direction": "long",
                    "timeframe_minutes": 1,
                    "entry_conditions": [
                        {"indicator": "close", "operator": "gt", "threshold": threshold}
                    ],
                    "exit_rules": [{"kind": "stop", "distance": 2.0}],
                    "tunable_parameters": [],
                    "regime_filters": [],
                    "session_filters": [],
                },
                complexity_limits=limits,
            )
            result = run_backtest(spec, candles, cost_model=CostModel())
            spec_hash = f"noise-{seed}-{variant}"
            ledger.append(
                ExperimentRecord(
                    spec_hash,
                    {"variant": variant},
                    start,
                    start + timedelta(days=1),
                    {"net_pnl": result.net_pnl},
                    holdout_accessed=True,
                )
            )
            metrics = {
                "trades": len(result.trades),
                "out_of_sample_trades": len(result.trades),
                "walk_forward_windows": 0,
                "validation_periods": 0,
                "out_of_sample_profit_factor": 0,
                "holdout_profit_factor": 0,
                "profitable_walk_forward_share": 0,
                "profitable_regimes": 0,
                "backtest_drawdown_pct": result.max_drawdown,
                "top5pct_trades_profit_share": 1,
                "neighbor_parameters_profitable": False,
                "p_value": 1,
                "cost_stress_net_positive": False,
            }
            if validate_gate1(
                metrics,
                holdout_spec_hash=spec_hash,
                ledger=ledger,
                config=config,
            ).passed:
                promotions += 1
        assert promotions == 0

