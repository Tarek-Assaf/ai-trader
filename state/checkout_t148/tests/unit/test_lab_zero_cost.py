from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

_path = Path(__file__).parents[2] / "scripts" / "lab_zero_cost.py"
_spec = importlib.util.spec_from_file_location("lab_zero_cost", _path)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
_trade_stats = _module._trade_stats
_with_cost_average = _module._with_cost_average
_zero_cost = _module._zero_cost


def test_zero_cost_model_is_explicitly_zero() -> None:
    model = _zero_cost("NSXUSD")
    assert model.for_bar("NSXUSD", datetime.now(UTC)) == (0.0, 0.0)
    assert model.provisional is False


def test_trade_stats_reports_points() -> None:
    trades = [SimpleNamespace(gross_pnl=-2.0), SimpleNamespace(gross_pnl=5.0)]
    result = SimpleNamespace(trades=trades)
    assert _trade_stats(result) == {
        "trades": 2,
        "average_profit_per_trade_points": 1.5,
        "best_trade_points": 5.0,
        "worst_trade_points": -2.0,
    }


def test_with_cost_average_prefers_stored_net() -> None:
    assert _with_cost_average({"metrics": {"trades": 4, "diag_net_pnl": 10}}) == 2.5
    assert (
        _with_cost_average(
            {"metrics": {"trades": 2, "diag_avg_gross_per_trade": 3, "diag_avg_cost_per_trade": 1}}
        )
        == 2.0
    )
