"""Static contract checks for the T-149 recorded-data diagnostic."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/lab_t149_recorded_tai.py"


def test_recorded_runner_has_six_required_groups_and_no_60m_run() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    constants = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"INSTRUMENTS", "TIMEFRAMES"}
    }
    assert constants["INSTRUMENTS"] == ("NAS100", "XAUUSD")
    assert constants["TIMEFRAMES"] == (1, 3, 5)


def test_runner_declares_recorded_side_fill_and_safety_outputs() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "features.ask[index].close" in source
    assert "features.bid[index].close" in source
    assert '"broker_calls": 0' in source
    assert '"orders": 0' in source
    assert '"sealed_holdout_read": False' in source
    assert '"gate1_evaluated": False' in source


def test_trade_csv_keeps_owner_comparison_columns() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    for column in (
        "signal_time_utc",
        "signal_time_riyadh",
        "entry",
        "stop",
        "tp1",
        "tp2",
        "tp3",
        "exit_reason",
        "result_points_real",
        "result_r_real",
        "result_points_dashboard",
        "result_r_dashboard",
        "spread_at_entry",
    ):
        assert f'"{column}"' in source
