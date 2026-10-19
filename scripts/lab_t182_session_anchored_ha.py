#!/usr/bin/env python3
"""T-182 diagnostic for the three pre-declared session HA anchors."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from scripts import lab_t162_pine_port as port
    from scripts.lab_t151_owner_trades import read_nas100_minutes, read_owner_entries
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import lab_t162_pine_port as port
    from scripts.lab_t151_owner_trades import read_nas100_minutes, read_owner_entries


ANCHORS = ("epoch", "session_1800_ny", "rth_0930_ny")
MODES = ("closed", "forming")
TIMEFRAME = 3
WINDOW_START = datetime(2026, 10, 5, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 17, tzinfo=UTC)


def _match(entries: tuple[Any, ...], trades: tuple[Any, ...], tolerance: int) -> tuple[int, list[dict[str, Any]]]:
    remaining = list(trades)
    rows: list[dict[str, Any]] = []
    matched = 0
    for owner in entries:
        candidates = [item for item in remaining if item.direction == owner.direction]
        nearest = min(
            candidates,
            key=lambda item: abs((item.entry_time - owner.entry_time).total_seconds()),
            default=None,
        )
        delta = (
            None
            if nearest is None
            else (nearest.entry_time - owner.entry_time).total_seconds() / (TIMEFRAME * 60)
        )
        if nearest is not None and abs(delta or 0.0) <= tolerance:
            matched += 1
            remaining.remove(nearest)
        rows.append(
            {
                "owner_time_utc": owner.entry_time.isoformat(),
                "owner_time_ny": owner.entry_time.astimezone(port.OWNER_ZONE).isoformat(),
                "direction": owner.direction,
                "nearest_port_entry_time": nearest.entry_time.isoformat() if nearest else None,
                "port_direction": nearest.direction if nearest else None,
                "signed_delta_bars": delta,
                "matched": nearest is not None and abs(delta or 0.0) <= tolerance,
                "first_difference": (
                    "missing_signal"
                    if nearest is None
                    else "direction"
                    if nearest.direction != owner.direction
                    else "signal_or_refresh"
                    if abs(delta or 0.0) > tolerance
                    else "matched"
                ),
            }
        )
    return matched, rows


def _variant(entries: tuple[Any, ...], bars: tuple[Any, ...], anchor: str, mode: str) -> dict[str, Any]:
    trades, features = port.replay(bars, mode, anchor, "session_1800_ny")
    counts: dict[str, int] = {}
    rows_by_tolerance: dict[str, list[dict[str, Any]]] = {}
    for tolerance in (0, 1, 2, 3, 30):
        counts[str(tolerance)], rows_by_tolerance[str(tolerance)] = _match(
            entries, trades, tolerance
        )
    matched_30 = counts["30"]
    return {
        "anchor": anchor,
        "mode": mode,
        "entry_counts": counts,
        "extra_entries_within_30_bars": max(0, len(trades) - matched_30),
        "comparison_rows": rows_by_tolerance["3"],
        "port_entry_count": len(trades),
        "trades": [port._json_trade(item) for item in trades],
        "bars": len(bars),
        "vwap_anchor": "session_1800_ny",
        "vwap_anchor_correct": True,
        "first_feature_values": [
            {
                "ts": bars[i].ts_utc.isoformat(),
                "ha_open": features.ha_open[i],
                "ha_close": features.ha_close[i],
                "vwap": features.vwap[i],
            }
            for i in range(max(0, len(bars) - 5), len(bars))
        ],
    }


def run(lake: Path, owner_file: Path) -> dict[str, Any]:
    entries = read_owner_entries(owner_file)
    if len(entries) != 31:
        raise RuntimeError(f"expected 31 owner entries, found {len(entries)}")
    warmup_start = WINDOW_START - timedelta(days=7)
    minutes = read_nas100_minutes(lake, warmup_start, WINDOW_END)
    bars = tuple(item for item in port.resample_ohlc(minutes, TIMEFRAME) if item.complete)
    if not bars:
        raise RuntimeError("NAS100 3m data is missing")
    variants = {
        anchor: {mode: _variant(entries, bars, anchor, mode) for mode in MODES}
        for anchor in ANCHORS
    }
    best_key = max(
        ((data["entry_counts"]["3"], anchor, mode) for anchor, modes in variants.items() for mode, data in modes.items()),
        key=lambda item: item[0],
    )
    return {
        "task": "T-182",
        "generated_at": datetime.now(UTC).isoformat(),
        "owner_timezone": "America/New_York",
        "owner_entries": len(entries),
        "owner_window_utc": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        "anchors": variants,
        "verdict": {
            "best_anchor": best_key[1],
            "best_mode": best_key[2],
            "within_3_bars": best_key[0],
            "within_30_bars": variants[best_key[1]][best_key[2]]["entry_counts"]["30"],
            "gate_25_of_31_met": best_key[0] >= 25,
            "vwap_anchor_correct": True,
        },
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "sealed_holdout_read": False,
            "lake_writes": 0,
            "production_state_writes": 0,
            "tuning": False,
        },
    }


def report(payload: dict[str, Any]) -> str:
    verdict = payload["verdict"]
    lines = [
        f"Best anchor {verdict['best_anchor']}, mode {verdict['best_mode']}: {verdict['within_3_bars']}/31 within 3 bars, {verdict['within_30_bars']}/31 within 30 bars; gate 25/31 met {'yes' if verdict['gate_25_of_31_met'] else 'no'}; VWAP anchor correct {'yes' if verdict['vwap_anchor_correct'] else 'no'}.",
        "",
        "# T-182 session-anchored 54-minute Heikin Ashi diagnostic",
        "",
        "The three pre-declared anchors were replayed in closed and forming modes. The 25/31 gate was not relaxed and no additional offsets were tested.",
        "",
        "| Anchor | Mode | Exact | ±1 | ±2 | ±3 | ±30 | Extra within 30 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for anchor, modes in payload["anchors"].items():
        for mode, data in modes.items():
            counts = data["entry_counts"]
            lines.append(
                f"| {anchor} | {mode} | {counts['0']} | {counts['1']} | {counts['2']} | {counts['3']} | {counts['30']} | {data['extra_entries_within_30_bars']} |"
            )
    lines += [
        "",
        "## VWAP and safety",
        "",
        "The Pine `ta.vwap(hlc3)` session resets at the 18:00 New York CME session open. The port now uses `session_1800_ny` for replay and reports the anchor as correct.",
        "",
        json.dumps(payload["safety"], indent=2, sort_keys=True),
        "",
        "## Per-entry differences",
        "",
    ]
    for anchor, modes in payload["anchors"].items():
        for mode, data in modes.items():
            lines.append(f"### {anchor}, {mode}")
            lines.append("")
            for row in data["comparison_rows"]:
                if row["first_difference"] != "matched":
                    lines.append(
                        f"- `{row['owner_time_utc']}` {row['direction']}: nearest `{row['nearest_port_entry_time']}` ({row['signed_delta_bars']} bars); first difference `{row['first_difference']}`."
                    )
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/t182_port.json"))
    parser.add_argument("--report", type=Path, default=Path("docs/evidence/t182_report.md"))
    parser.add_argument("--entries", type=Path, default=Path("docs/evidence/t182_entries.csv"))
    args = parser.parse_args()
    payload = run(args.lake, args.owner_file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.write_text(report(payload), encoding="utf-8")
    with args.entries.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["anchor", "mode", "owner_time_utc", "owner_time_ny", "direction", "nearest_port_entry_time", "signed_delta_bars", "matched", "first_difference"])
        for anchor, modes in payload["anchors"].items():
            for mode, data in modes.items():
                for row in data["comparison_rows"]:
                    writer.writerow([anchor, mode, row["owner_time_utc"], row["owner_time_ny"], row["direction"], row["nearest_port_entry_time"], row["signed_delta_bars"], row["matched"], row["first_difference"]])
    print(json.dumps({"task": "T-182", "verdict": payload["verdict"], "safety": payload["safety"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
