from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

_path = Path(__file__).parents[2] / "scripts" / "lab_recent_zero_cost.py"
_spec = importlib.util.spec_from_file_location("lab_recent_zero_cost", _path)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules["lab_recent_zero_cost"] = _module
_spec.loader.exec_module(_module)


def test_trade_stats_uses_net_points_and_profit_factor() -> None:
    result = SimpleNamespace(
        trades=[
            SimpleNamespace(net_pnl=-2.0),
            SimpleNamespace(net_pnl=5.0),
            SimpleNamespace(net_pnl=1.0),
        ],
        max_drawdown=2.5,
    )
    assert _module._trade_stats(result) == {
        "trades": 3,
        "average_trade_points": 4 / 3,
        "best_trade_points": 5.0,
        "worst_trade_points": -2.0,
        "net_points": 4.0,
        "profit_factor": 3.0,
        "max_drawdown_points": 2.5,
    }


def test_candidate_rebuild_is_deterministic_and_marks_sources() -> None:
    first = _module._candidate_specs("NSXUSD", 60, staged_generations=1, max_staged_parents=2)
    second = _module._candidate_specs("NSXUSD", 60, staged_generations=1, max_staged_parents=2)
    assert len(first) == len(second)
    assert [(_module.spec_hash(item["spec"]), item["source"]) for item in first] == [
        (_module.spec_hash(item["spec"]), item["source"]) for item in second
    ]
    assert {item["source"] for item in first} == {"round_d", "staged"}
    assert all(item["spec"].instrument == "NSXUSD" for item in first)


def test_group_summary_reports_zero_and_normal_cost_counts() -> None:
    rows = [
        {
            "source": "round_d",
            "zero_cost": {
                "trades": 300,
                "average_trade_points": 1.0,
                "best_trade_points": 4.0,
                "worst_trade_points": -2.0,
                "net_points": 300.0,
                "profit_factor": 2.0,
                "max_drawdown_points": 2.0,
            },
            "normal_cost": {
                "trades": 300,
                "average_trade_points": 0.5,
                "best_trade_points": 3.5,
                "worst_trade_points": -2.5,
                "net_points": 150.0,
                "profit_factor": 1.5,
                "max_drawdown_points": 3.0,
            },
            "strategy_hash": "a",
            "rules": "rules",
        },
        {
            "source": "staged",
            "zero_cost": {
                "trades": 2,
                "average_trade_points": -1.0,
                "best_trade_points": 1.0,
                "worst_trade_points": -3.0,
                "net_points": -2.0,
                "profit_factor": 0.5,
                "max_drawdown_points": 3.0,
            },
            "normal_cost": {
                "trades": 2,
                "average_trade_points": -2.0,
                "best_trade_points": 0.0,
                "worst_trade_points": -4.0,
                "net_points": -4.0,
                "profit_factor": 0.0,
                "max_drawdown_points": 4.0,
            },
            "strategy_hash": "b",
            "rules": "rules2",
        },
    ]
    summary = _module._group_summary(rows, min_trades=300)
    assert summary["strategies_rebuilt"] == 2
    assert summary["by_source"] == {"round_d": 1, "staged": 1}
    assert summary["zero_cost"]["total_trades"] == 302
    assert summary["zero_cost"]["strategies_meeting_minimum_trade_count"] == 1
    assert summary["zero_cost"]["profitable_strategies"] == 1
    assert summary["normal_cost"]["average_trade_points"] == 146 / 302
