import random
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.backtest import CostModel, run_backtest
from trader.lab.ledger import ExperimentLedger, ExperimentRecord
from trader.lab.metrics import gate1_metrics
from trader.lab.miner import PATTERN_NAMES, PatternMiner, mine_patterns
from trader.lab.spec import ComplexityLimits, parse_strategy_spec
from trader.lab.validator import Gate1Config, ValidationResult, validate_gate1


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

    def reject(metrics: Mapping[str, object], *, holdout_spec_hash: str) -> ValidationResult:
        return ValidationResult(False, ("test",), 0.0, 1, holdout_spec_hash, False, metrics)

    result = PatternMiner(ledger=ledger).scan(_candles(), validator=reject)

    assert {candidate.name for candidate in result.candidates} == set(PATTERN_NAMES)
    assert result.hypotheses_tested == 20
    assert len(result.candidates) == 20
    assert len(ledger.records()) == result.hypotheses_tested
    assert result.promoted == ()
    assert all(candidate.strategy.instrument == "NSXUSD" for candidate in result.candidates)


def test_miner_uses_validator_only_after_ledger_append() -> None:
    ledger = ExperimentLedger()
    seen_counts: list[int] = []

    def validator(metrics: Mapping[str, object], *, holdout_spec_hash: str) -> ValidationResult:
        seen_counts.append(ledger.count())
        passed = metrics["pattern"] == "mean_reversion"
        return ValidationResult(
            passed,
            () if passed else ("test",),
            0.0,
            ledger.count(),
            holdout_spec_hash,
            False,
            metrics,
        )

    result = mine_patterns(_candles(), ledger=ledger, validator=validator)

    assert seen_counts == list(range(1, 21))
    assert {candidate.name for candidate in result.promoted} == {"mean_reversion"}


def test_parallel_mining_matches_serial_order_and_metrics() -> None:
    serial = PatternMiner(ledger=ExperimentLedger(), workers=1).scan(_candles())
    parallel = PatternMiner(ledger=ExperimentLedger(), workers=2).scan(_candles())

    assert [item.strategy_hash for item in parallel.candidates] == [
        item.strategy_hash for item in serial.candidates
    ]
    assert [item.metrics for item in parallel.candidates] == [
        item.metrics for item in serial.candidates
    ]
    assert [item.signals for item in parallel.candidates] == [
        item.signals for item in serial.candidates
    ]


def test_mining_reports_completed_spec_progress() -> None:
    updates: list[tuple[int, int]] = []
    PatternMiner(
        ledger=ExperimentLedger(),
        workers=1,
        progress_callback=lambda done, total: updates.append((done, total)),
    ).scan(_candles())

    assert updates == [(index, 20) for index in range(1, 21)]


def test_miner_is_safe_for_empty_input_when_instrument_is_explicit() -> None:
    result = PatternMiner(instrument="XAUUSD").scan(())

    assert len(result.candidates) == 20
    assert all(item.strategy.instrument == "XAUUSD" for item in result.candidates)


def test_derived_metrics_noise_variants_never_promote() -> None:
    """Real config gates reject derived metrics for 2,000 random variants."""
    limits = ComplexityLimits(max_conditions_per_strategy=5, max_tunable_parameters=4)
    try:
        config = Gate1Config.from_config()
    except ValueError:
        # The bundled desktop runtime lacks PyYAML; CI uses the checked-in
        # strategy_lab.yaml. Keep the local smoke runnable with equivalent
        # fail-closed floors rather than inventing candidate metrics.
        config = Gate1Config(300, 100, 5, 3, 1.3, 1.2, 0.7, 50, True, True, 2, 0.4, 0.05)
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
            stress = run_backtest(spec, candles, cost_model=CostModel(), cost_stress_pct=50.0)
            spec_hash = f"noise-{seed}-{variant}"
            ledger.append(
                ExperimentRecord(
                    spec_hash,
                    {"variant": variant},
                    start,
                    start + timedelta(days=1),
                    gate1_metrics(result, cost_stress=stress),
                    holdout_accessed=True,
                )
            )
            if validate_gate1(
                gate1_metrics(result, cost_stress=stress),
                holdout_spec_hash=spec_hash,
                ledger=ledger,
                config=config,
            ).passed:
                promotions += 1
        assert promotions == 0


def test_planted_upward_pattern_is_promoted_but_noise_is_not() -> None:
    """A clear opening-range drift survives the same derived-metric gate."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    planted: list[Candle] = []
    price = 100.0
    for index in range(960):
        if index >= 30:
            price += 0.35
        planted.append(
            Candle(
                start + timedelta(minutes=index),
                price,
                price + 0.15,
                price - 0.15,
                price,
                1.0,
                ("NAS100",),
            )
        )

    def run(promote_candles: list[Candle]) -> set[str]:
        ledger = ExperimentLedger()
        config = Gate1Config(1, 1, 1, 1, 1.2, 1.2, 0.5, 0, False, True, 0, 0.8, 50)

        def validate(metrics: Mapping[str, object], *, holdout_spec_hash: str) -> ValidationResult:
            ledger.append(
                ExperimentRecord(
                    holdout_spec_hash,
                    {},
                    start,
                    start + timedelta(days=1),
                    {},
                    holdout_accessed=True,
                )
            )
            return validate_gate1(
                metrics,
                holdout_spec_hash=holdout_spec_hash,
                ledger=ledger,
                config=config,
            )

        result = PatternMiner(
            ledger=ledger,
            train_bars=120,
            test_bars=60,
            step_bars=60,
            cost_stress_pct=0,
            validator=validate,
        ).scan(promote_candles)
        return {candidate.name for candidate in result.promoted}

    assert "opening_range" in run(planted)
    randomizer = random.Random(22)
    noise: list[Candle] = []
    price = 100.0
    for index in range(960):
        price += randomizer.uniform(-0.3, 0.3)
        noise.append(
            Candle(
                start + timedelta(minutes=index),
                price,
                price + 0.15,
                price - 0.15,
                price,
                1.0,
                ("NAS100",),
            )
        )
    assert run(noise) == set()
