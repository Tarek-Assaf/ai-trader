"""Pure Strategy Lab gate, ledger and allocation fixtures."""

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from trader.data.resample import Candle
from trader.lab.allocator import allocate
from trader.lab.backtest import CostModel, run_backtest
from trader.lab.decay import DecayMonitor
from trader.lab.ledger import ExperimentLedger, ExperimentRecord
from trader.lab.promotion import PromotionGate
from trader.lab.spec import ComplexityLimits, parse_strategy_spec
from trader.lab.validator import (
    Gate1Config,
    HoldoutAccessError,
    HoldoutReader,
    ValidationResult,
    validate_gate1,
)


def _record(index: int = 0) -> ExperimentRecord:
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    return ExperimentRecord("hash", {"variant": index}, stamp, stamp + timedelta(days=1), {})


def test_ledger_counts_every_variant_and_rejects_duplicate_ids():
    ledger = ExperimentLedger()
    record = _record()
    ledger.append(record)
    assert ledger.count() == 1
    with pytest.raises(ValueError, match="append-only"):
        ledger.append(record)


def test_multiple_testing_gate_rejects_inadequate_random_result():
    metrics = {
        "trades": 300,
        "out_of_sample_trades": 100,
        "walk_forward_windows": 5,
        "validation_periods": 3,
        "out_of_sample_profit_factor": 1.4,
        "holdout_profit_factor": 1.3,
        "profitable_walk_forward_share": 0.8,
        "profitable_regimes": 2,
        "backtest_drawdown_pct": 5,
        "top5pct_trades_profit_share": 0.2,
        "neighbor_parameters_profitable": True,
        "p_value": 0.01,
        "cost_stress_net_positive": True,
    }
    ledger = ExperimentLedger(
        [
            ExperimentRecord(
                "noise",
                {},
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
                {},
                holdout_accessed=True,
            )
        ]
    )
    config = Gate1Config(300, 100, 5, 3, 1.3, 1.2, 0.7, 50, True, True, 2, 0.4, 20)
    assert validate_gate1(
        metrics,
        holdout_spec_hash="noise",
        trials=1,
        ledger=ledger,
        config=config,
    ).passed
    result = validate_gate1(
        metrics,
        holdout_spec_hash="noise",
        trials=10_000,
        ledger=ledger,
        config=config,
    )
    assert not result.passed
    assert "adjusted_significance_failed" in result.reasons


def test_holdout_reader_rejects_path_escape(tmp_path):
    root = tmp_path / "sealed"
    root.mkdir()
    (root / "data.json").write_text("{}", encoding="utf-8")
    ledger = ExperimentLedger()
    reader = HoldoutReader(root, ledger=ledger)
    assert reader.read("hash", root / "data.json") == b"{}"
    with pytest.raises(HoldoutAccessError):
        reader.read("other", tmp_path / "outside.json")
    with pytest.raises(HoldoutAccessError):
        HoldoutReader(root, ledger=ledger).read("hash", root / "data.json")


def test_only_validator_defines_holdout_reader():
    source_root = Path(__file__).parents[2] / "src" / "trader"
    offenders = []
    for path in source_root.rglob("*.py"):
        if path.name == "validator.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "HoldoutReader" in text or "sealed_holdout" in text:
            offenders.append(path)
    assert offenders == []


def test_promotion_audit_and_live_gate():
    events: list[str] = []
    gate = PromotionGate(
        {
            "stage_capital": {"micro_live_size_pct_of_target": 20},
            "gate1_backtest_to_paper": {"min_trades": 0},
        },
        alert=events.append,
    )
    validation = ValidationResult(True, (), 0.05, 1, "s1", True)
    decision = gate.promote(
        "s1",
        "backtest",
        {"strategy_hash": "s1", "trades": 0},
        target_stage="paper",
        validation=validation,
    )
    assert decision.approved and decision.stage == "paper"
    assert not gate.promote(
        "s1",
        "backtest",
        {"trades": 0},
        target_stage="paper",
        validation=ValidationResult(True, (), 0.05, 1, "other", True),
    ).approved
    assert not gate.promote(
        "s1",
        "backtest",
        {"trades": 0},
        target_stage="paper",
        validation=ValidationResult(False, ("failed",), 0.05, 1, "s1", True),
    ).approved
    blocked = gate.promote("s1", "paper", {}, target_stage="micro_live")
    assert not blocked.approved and blocked.reasons == ("live_trading_disabled",)


def test_promotion_writes_real_audit_event_and_sql_ledger_round_trips():
    from sqlalchemy import create_engine, select

    from trader.core.audit import SqlAuditWriter
    from trader.core.db import metadata, tables
    from trader.lab.ledger import SqlExperimentLedger

    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    audit = SqlAuditWriter(engine)
    gate = PromotionGate({"gate1_backtest_to_paper": {"min_trades": 0}}, audit=audit)
    validation = ValidationResult(True, (), 0.05, 1, "s1", True)
    assert gate.promote(
        "s1", "backtest", {"trades": 0}, target_stage="paper", validation=validation
    ).approved
    record = _record()
    ledger = SqlExperimentLedger(engine)
    ledger.append(record)
    assert ledger.count() == 1 and ledger.records()[0].experiment_id == record.experiment_id
    with engine.connect() as connection:
        events = connection.execute(select(tables["audit_log"].c.event_type)).all()
    assert [item[0] for item in events] == ["strategy_stage_changed"]


def test_overfit_in_sample_result_fails_holdout_gate():
    config = Gate1Config(1, 1, 1, 1, 1.0, 1.2, 0.5, 50, True, True, 1, 0.9, 20)
    metrics = {
        "trades": 10,
        "out_of_sample_trades": 10,
        "walk_forward_windows": 2,
        "validation_periods": 1,
        "out_of_sample_profit_factor": 2.0,
        "holdout_profit_factor": 0.8,
        "profitable_walk_forward_share": 1.0,
        "profitable_regimes": 1,
        "backtest_drawdown_pct": 1,
        "top5pct_trades_profit_share": 0.2,
        "neighbor_parameters_profitable": True,
        "p_value": 0.001,
        "cost_stress_net_positive": True,
    }
    ledger = ExperimentLedger(
        [
            ExperimentRecord(
                "overfit",
                {},
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
                {},
                holdout_accessed=True,
            )
        ]
    )
    result = validate_gate1(metrics, holdout_spec_hash="overfit", ledger=ledger, config=config)
    assert not result.passed and "holdout_profit_factor_below_gate" in result.reasons


def test_seeded_random_noise_variants_never_promote():
    limits = ComplexityLimits(max_conditions_per_strategy=5, max_tunable_parameters=4)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    config = Gate1Config(300, 100, 5, 3, 1.3, 1.2, 0.7, 50, True, True, 2, 0.4, 20)
    for seed in (7, 19, 31):
        randomizer = random.Random(seed)
        price = 100.0
        candles = []
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
                    ("NAS100", "bid"),
                )
            )
        ledger = ExperimentLedger()
        promotions = 0
        for variant in range(2_000):
            threshold = 95 + randomizer.random() * 10
            spec = parse_strategy_spec(
                {
                    "schema_version": 1,
                    "instrument": "NAS100",
                    "direction": "long",
                    "timeframe_minutes": 1,
                    "entry_conditions": [
                        {"indicator": "close", "operator": "gt", "threshold": threshold}
                    ],
                    "exit_rules": [{"kind": "stop", "distance": 2}],
                    "tunable_parameters": [],
                    "regime_filters": [],
                    "session_filters": [],
                },
                complexity_limits=limits,
            )
            result = run_backtest(spec, candles, cost_model=CostModel())
            record = ExperimentRecord(
                f"noise-{seed}-{variant}",
                {"variant": variant},
                start,
                start + timedelta(days=1),
                {},
            )
            ledger.append(
                ExperimentRecord(
                    record.strategy_hash,
                    record.parameters,
                    record.data_start,
                    record.data_end,
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
                holdout_spec_hash=record.strategy_hash,
                ledger=ledger,
                config=config,
            ).passed:
                promotions += 1
        assert promotions == 0


def test_decay_retires_after_second_recent_demotion_and_allocator_diversifies():
    now = datetime(2026, 1, 10, tzinfo=UTC)
    monitor = DecayMonitor(
        window_trades=30, max_drawdown_ratio=1.0, size_reduction=0.5, retire_days=90
    )
    decision = monitor.evaluate(
        "s1", (-3, -1), backtest_drawdown=1, demotions=(now - timedelta(days=2),), now=now
    )
    assert decision.demote and decision.retire
    old = monitor.evaluate(
        "s1", (-3, -1), backtest_drawdown=1, demotions=(now - timedelta(days=91),), now=now
    )
    assert old.demote and not old.retire
    result = allocate(
        [
            {"strategy_id": "a", "score": 2, "features": ["trend"]},
            {"strategy_id": "b", "score": 1, "features": ["mean_reversion"]},
        ],
        risk_budget=Decimal("0.03"),
        max_concurrent=2,
        min_diversity=0.3,
    )
    assert {item.strategy_id for item in result} == {"a", "b"}
    assert sum(item.risk_budget for item in result) == Decimal("0.03")
