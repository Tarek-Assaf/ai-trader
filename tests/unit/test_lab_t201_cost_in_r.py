import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.lab_t201_cost_in_r import (
    _distribution,
    _owner_trades,
    _percentile,
    _t200_passes,
)


def test_percentile_interpolates_and_empty_is_none() -> None:
    assert _percentile([], 0.5) is None
    assert _percentile([1.0, 3.0], 0.25) == 1.5


def test_distribution_reports_cost_to_r_shares() -> None:
    summary = _distribution(
        [
            {"risk_points": 1.0, "cost_points": 2.0},
            {"risk_points": 10.0, "cost_points": 1.0},
        ]
    )
    assert summary["trades"] == 2
    assert summary["share_cost_over_0_25_r"] == 0.5
    assert summary["share_cost_over_1_r"] == 0.5


def test_t200_guard_is_fail_closed_without_tick_and_exact_limit_passes() -> None:
    trade = {"risk_points": 4.0, "spread_points": 1.0}
    assert _t200_passes(trade, None) is None
    assert _t200_passes(trade, 0.5) is True
    assert _t200_passes({"risk_points": 4.0, "spread_points": 1.01}, 0.5) is False


def test_owner_trade_loader_pairs_entry_and_exit(tmp_path: Path) -> None:
    path = tmp_path / "owner.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Trade number", "Type", "Price USD", "Net PnL USD", "Date and time"])
        writer.writerow(["1", "Entry short", "100", "-40", "2026-10-06 16:03"])
        writer.writerow(["1", "Exit short", "102", "-40", "2026-10-06 18:03"])
    trades = _owner_trades(path)
    assert len(trades) == 1
    assert trades[0]["risk_points"] == 2.0
    assert trades[0]["gross_r"] == -1.0
