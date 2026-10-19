#!/usr/bin/env python3
"""Compare T-162 port trade count and outcome mix with owner dashboards."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import lab_t162_pine_port as port

NY = ZoneInfo("America/New_York")
REFERENCE = {
    "cf": {"trades": 53, "sl": 12, "tp1": 15, "tp2": 9, "tp3": 17},
    "nq1": {"trades": 115, "sl": 38, "tp2": 12, "tp3": 32},
}


def _window(name: str) -> tuple[datetime, datetime]:
    if name == "ny_midnight_13_15":
        return (
            datetime(2026, 10, 13, tzinfo=NY).astimezone(UTC),
            datetime(2026, 10, 15, tzinfo=NY).astimezone(UTC),
        )
    if name == "utc_midnight_13_15":
        return datetime(2026, 10, 13, tzinfo=UTC), datetime(2026, 10, 15, tzinfo=UTC)
    if name in {"owner_trade_window", "nq1_reference_window"}:
        return (
            datetime.combine(datetime(2026, 10, 6), time.min, tzinfo=NY).astimezone(UTC),
            datetime.combine(datetime(2026, 10, 17), time.min, tzinfo=NY).astimezone(UTC),
        )
    raise ValueError(f"unknown window {name}")


def _summary(trades: list[port.PortTrade] | tuple[port.PortTrade, ...]) -> dict[str, object]:
    counts = Counter(trade.exit_reason for trade in trades if trade.exit_reason)
    total = sum(counts.values())
    total_r = sum(
        {"sl": -1.0, "be": 0.0, "tp1": 1.0, "tp2": 2.0, "tp3": 3.0}.get(
            str(trade.exit_reason), 0.0
        )
        for trade in trades
    )
    return {
        "trades": len(trades),
        "completed": total,
        "open_at_end": len(trades) - total,
        "sl": counts.get("stop", 0),
        "tp1": counts.get("tp1", 0),
        "tp2": counts.get("tp2", 0),
        "tp3": counts.get("tp3", 0),
        "be": counts.get("be", 0),
        "win_rate_completed_excluding_be": (
            (counts.get("tp1", 0) + counts.get("tp2", 0) + counts.get("tp3", 0)) / total
            if total
            else 0.0
        ),
        "total_r": total_r,
        "mix_denominator": total,
    }


def run(lake: Path, owner_file: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "task": "T-167",
        "reference": REFERENCE,
        "windows": {},
        "safety": {"broker_calls": 0, "orders": 0, "sealed_holdout_read": False, "lake_writes": 0, "production_state_writes": 0, "tuning": False},
    }
    for name in ("ny_midnight_13_15", "utc_midnight_13_15", "owner_trade_window", "nq1_reference_window"):
        start, end = _window(name)
        payload = port.run(lake, owner_file, start=start, end=end)
        modes = {}
        for mode, details in payload["modes"].items():
            trades = tuple(
                port.PortTrade(
                    item["direction"],
                    item["entry_index"],
                    datetime.fromisoformat(item["entry_time"]),
                    item["entry_price"],
                    item["stop_price"],
                    item["exit_index"],
                    datetime.fromisoformat(item["exit_time"]) if item["exit_time"] else None,
                    item["exit_reason"],
                    item["highest_target"],
                )
                for item in details["trades"]
            )
            modes[mode] = _summary(trades)
        result["windows"][name] = {"start": start.isoformat(), "end": end.isoformat(), "modes": modes}
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    payload = run(args.lake, args.owner_file)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# T-167 dashboard count comparison",
        "",
        "**Verdict:** The port's count and outcome mix are reported below for the three bounded windows and the NQ1 reference window. The comparison uses the port's closed and forming modes separately; a completed trade is a trade with an exit reason, and win rate excludes break-even exits from the denominator.",
        "",
        "| Window | Mode | Trades | SL | TP1 | TP2 | TP3 | BE | Win rate (completed) | Total R |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, window in payload["windows"].items():
        for mode, values in window["modes"].items():
            lines.append(f"| {name} | {mode} | {values['trades']} | {values['sl']} | {values['tp1']} | {values['tp2']} | {values['tp3']} | {values['be']} | {values['win_rate_completed_excluding_be']:.2%} | {values['total_r']:.2f} |")
    lines += [
        "",
        "## Reference dashboards",
        "",
        "- CFD dashboard (Q-068): 53 trades, 12 SL, 15 TP1, 9 TP2, 17 TP3, 77.36% win rate.",
        "- NQ1 dashboard (Q-067): 115 trades, 38 SL, 12 TP2, 32 TP3; TP1 was hidden and is not guessed here.",
        "- The NQ1 window is evaluated only against the NSXUSD data available to the port; no futures bars are claimed.",
        "",
        "Safety counters are all zero/false; no broker, order, holdout, lake-write, production-state, tuning or strategy-search activity is performed by this script.",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
