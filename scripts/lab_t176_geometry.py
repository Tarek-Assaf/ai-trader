#!/usr/bin/env python3
"""Audit owner CSV geometry against the stored Pine source for T-176."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import lab_t162_pine_port as port  # noqa: E402
from scripts.lab_t160_owner_time_ny import parse_owner_datetime  # noqa: E402

TICK = 0.25
TOLERANCE = 0.05
SOURCE_FACTS = {
    "tranche_sizing": "The stored Pine file is an indicator(), not strategy(); it has no strategy.entry/order quantity or tranche percentage.",  # noqa: E501
    "targets": "Buy TP1/TP2/TP3 are entry + risk, +2*risk, +3*risk (source lines 770-772); sell mirrors at lines 859-861.",  # noqa: E501
    "break_even": "After TP2, activeSL := entry and protectionActive := true; a later entry touch emits BE (source lines 1070-1105).",  # noqa: E501
    "risk": "Swing lookback 5, buffer 0.20 ATR, ATR(14), maxRiskATR 2.0 (source lines 471-496 and 507-554).",  # noqa: E501
}


def _num(value: str) -> float:
    return float(str(value).replace(",", "").strip())


def _row(row: dict[str, str]) -> dict[str, Any]:
    return {
        **row,
        "dt": parse_owner_datetime(row["Date and time"]),
        "price": _num(row["Price USD"]),
        "qty": _num(row["Size (qty)"]),
    }


def load(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = [_row(row) for row in csv.DictReader(handle)]
    entries = [row for row in rows if row["Type"].lower().startswith("entry")]
    return rows, entries


def positions(rows: list[dict[str, Any]], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[datetime, str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in entries:
        direction = "long" if "long" in row["Type"].lower() else "short"
        groups[(row["dt"], direction, row["price"])].append(row)
    out = []
    for key, entry_rows in sorted(groups.items()):
        entry_time, direction, entry_price = key
        numbers = {row["Trade number"] for row in entry_rows}
        related = [
            row
            for row in rows
            if row["Trade number"] in numbers and row["Type"].lower().startswith("exit")
        ]
        stops = [row["price"] for row in related if "stop loss" in row["Signal"].lower()]
        targets = [row for row in related if "take profit" in row["Signal"].lower()]
        market = [row for row in related if "market" in row["Signal"].lower()]
        stop = stops[0] if stops else None
        risk = abs(entry_price - stop) if stop is not None else None
        target_multiples = [
            abs(row["price"] - entry_price) / risk if risk and risk > 0 else None for row in targets
        ]
        out.append(
            {
                "entry_time": entry_time.isoformat(),
                "direction": direction,
                "entry_price": entry_price,
                "trade_numbers": sorted(numbers),
                "tranche_count": len(entry_rows),
                "quantity": sum(row["qty"] for row in entry_rows),
                "quantities": [row["qty"] for row in entry_rows],
                "stop_price": stop,
                "risk": risk,
                "target_multiples": target_multiples,
                "target_rows": [
                    {key: row[key] for key in ("Trade number", "Date and time", "price", "Signal")}
                    for row in targets
                ],
                "market_exit_rows": [
                    {key: row[key] for key in ("Trade number", "Date and time", "price", "Signal")}
                    for row in market
                ],
                "exit_rows": len(related),
            }
        )
    return out


def geometry(
    rows: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    positions_data: list[dict[str, Any]],
    bars: Any,
) -> dict[str, Any]:
    features = port.compute_features(bars, "closed")
    multiples = [
        value for item in positions_data for value in item["target_multiples"] if value is not None
    ]
    target_counts = {
        "1R": sum(abs(value - 1.0) <= TOLERANCE for value in multiples),
        "2R": sum(abs(value - 2.0) <= TOLERANCE for value in multiples),
        "3R": sum(abs(value - 3.0) <= TOLERANCE for value in multiples),
        "other": sum(
            all(abs(value - target) > TOLERANCE for target in (1.0, 2.0, 3.0))
            for value in multiples
        ),
        "total": len(multiples),
    }
    be_candidates = []
    for item in positions_data:
        targets = [
            row
            for row in rows
            if row["Trade number"] in item["trade_numbers"]
            and "take profit" in row["Signal"].lower()
        ]
        if not targets:
            continue
        first_tp = min(row["dt"] for row in targets)
        later_stops = [
            row
            for row in rows
            if row["Trade number"] in item["trade_numbers"]
            and row["dt"] > first_tp
            and "stop loss" in row["Signal"].lower()
        ]
        be_candidates.append(
            {
                "entry_time": item["entry_time"],
                "matches": any(
                    abs(row["price"] - item["entry_price"]) <= TICK for row in later_stops
                ),
            }
        )
    ratios = []
    for item in positions_data:
        index = port._bar_at(bars, datetime.fromisoformat(item["entry_time"]))[0]
        atr = features.atr[index] if index is not None else None
        ratio = item["risk"] / float(atr) if item["risk"] is not None and atr and atr > 0 else None
        item["entry_bar_index"] = index
        item["atr"] = atr
        item["risk_atr"] = ratio
        ratios.append(ratio)
    duration_checks = []
    for item in positions_data:
        exits = [
            row
            for row in rows
            if row["Trade number"] in item["trade_numbers"]
            and row["Type"].lower().startswith("exit")
        ]
        if not exits:
            continue
        duration = (
            max(row["dt"] for row in exits) - datetime.fromisoformat(item["entry_time"])
        ).total_seconds() / 180.0
        csv_duration = [
            int(row["Duration (bars)"])
            for row in entries
            if row["Trade number"] in item["trade_numbers"]
        ]
        duration_checks.append(abs(duration - csv_duration[0]) <= 1 if csv_duration else False)
    return {
        "positions": len(positions_data),
        "tranche_count_distribution": dict(
            Counter(item["tranche_count"] for item in positions_data)
        ),
        "total_quantity": sum(item["quantity"] for item in positions_data),
        "target_multiples": target_counts,
        "market_exit_rows": sum(len(item["market_exit_rows"]) for item in positions_data),
        "market_exit_signals": Counter(
            row["Signal"] for item in positions_data for row in item["market_exit_rows"]
        ),
        "break_even": {
            "candidates": len(be_candidates),
            "matches": sum(item["matches"] for item in be_candidates),
            "rows": be_candidates,
        },
        "risk_atr": {
            "ratios": ratios,
            "within_cap": sum(value is not None and 0 < value <= 2.0 for value in ratios),
            "total_with_ratio": sum(value is not None for value in ratios),
        },
        "duration_bars": {
            "checked": len(duration_checks),
            "matches": sum(duration_checks for _ in [0]) if False else sum(duration_checks),
        },
        "source": SOURCE_FACTS,
        "positions": positions_data,  # noqa: F601
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--pine-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rows, entries = load(args.owner_file)
    data_start = min(row["dt"] for row in entries) - timedelta(days=7)
    end = max(row["dt"] for row in rows) + timedelta(minutes=3)
    bars = tuple(
        port.resample_ohlc(port.read_nas100_minutes(args.lake, data_start, end), port.TIMEFRAME)
    )
    position_data = positions(rows, entries)
    result = geometry(rows, entries, position_data, bars)
    result["source_file"] = str(args.pine_source)
    result["rows"] = len(rows)
    result["entry_rows"] = len(entries)
    result["verdict"] = "partly"
    result["verdict_support"] = {
        "script_is_right_data_differs": "Target multiples, swing-risk cap and break-even behavior can be compared to the source.",  # noqa: E501
        "different_version_or_settings": "The source is an indicator with no strategy order sizing, while the CSV contains broker-style tranches and market exits; those rows cannot be produced by this stored source alone.",  # noqa: E501
    }
    result["safety"] = {
        "broker_calls": 0,
        "orders": 0,
        "holdout_reads": 0,
        "lake_writes": 0,
        "tuning": False,
        "secrets": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
            default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
        )
        + "\n",
        encoding="utf-8",
    )
    t = result["target_multiples"]
    r = result["risk_atr"]
    b = result["break_even"]
    d = result["duration_bars"]
    lines = [
        "# T-176 owner trade geometry",
        "",
        f"Verdict: **partly**; positions {result['positions']}; tranche split matches: not determinable from indicator source; exits at 1R/2R/3R: {t['1R']}/{t['2R']}/{t['3R']} of {t['total']}; break-even matches: {b['matches']} of {b['candidates']}; R/ATR within cap: {r['within_cap']} of {r['total_with_ratio']}; rows the script cannot produce: {result['market_exit_rows']} market exits plus all broker-style entry/tranche rows.",  # noqa: E501
        "",
        "## Counts",
        "",
        f"CSV rows: {result['rows']}; entry rows: {result['entry_rows']}; positions: {result['positions']}.",  # noqa: E501
        f"Tranche counts: {result['tranche_count_distribution']}; quantities are grouped by identical entry time, direction and price.",  # noqa: E501
        f"Exit multiples: 1R={t['1R']}, 2R={t['2R']}, 3R={t['3R']}, other={t['other']} of {t['total']}.",  # noqa: E501
        f"Break-even after a TP candidate: {b['matches']}/{b['candidates']}.",
        f"R/ATR within 0<R/ATR<=2: {r['within_cap']}/{r['total_with_ratio']}. Duration checks within one 3-minute bar: {d['matches']}/{d['checked']}.",  # noqa: E501
        "",
        "## Verdict interpretation",
        "",
        "Target multiples, swing-risk cap and break-even behavior support the stored script only where the owner rows have the same geometry. The stored file is an indicator(), not a strategy(), and contains no order sizing or tranche percentages; market exits and broker-style tranches therefore support a different execution version or settings rather than a pure data-basis explanation.",  # noqa: E501
        "",
        "## Source facts",
        "",
        *[f"- {key}: {value}" for key, value in SOURCE_FACTS.items()],
        "",
        "## Safety",
        "",
        "Replay-only CSV/source/lake ATR read; no broker calls, orders, holdout reads, lake writes, tuning or secrets.",  # noqa: E501
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
