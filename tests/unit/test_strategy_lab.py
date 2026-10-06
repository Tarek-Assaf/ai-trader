"""Pure Strategy Lab gate, ledger and allocation fixtures."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from trader.lab.allocator import allocate
from trader.lab.decay import DecayMonitor
from trader.lab.ledger import ExperimentLedger, ExperimentRecord
from trader.lab.promotion import PromotionGate
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
        "multiple_testing_corrected": True,
        "p_value": 0.01,
    }
    result = validate_gate1(
        metrics,
        trials=10_000,
        config=Gate1Config(300, 100, 5, 3, 1.3, 1.2, 0.7, 50, True, True, 2, 0.4, 20),
    )
    assert not result.passed
    assert "adjusted_significance_failed" in result.reasons


def test_holdout_reader_rejects_path_escape(tmp_path):
    root = tmp_path / "sealed"
    root.mkdir()
    (root / "data.json").write_text("{}", encoding="utf-8")
    reader = HoldoutReader(root)
    assert reader.read("hash", root / "data.json") == b"{}"
    with pytest.raises(HoldoutAccessError):
        reader.read("other", tmp_path / "outside.json")


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
    validation = ValidationResult(True, (), 0.05, 1, metrics={"strategy_hash": "s1"})
    decision = gate.promote(
        "s1",
        "backtest",
        {"strategy_hash": "s1", "trades": 0},
        target_stage="paper",
        validation=validation,
    )
    assert decision.approved and decision.stage == "paper"
    blocked = gate.promote("s1", "paper", {}, target_stage="micro_live")
    assert not blocked.approved and blocked.reasons == ("live_trading_disabled",)


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
