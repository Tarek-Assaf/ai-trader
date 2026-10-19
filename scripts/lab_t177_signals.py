#!/usr/bin/env python3
"""Signal coincidence permutation diagnostic for T-177."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import lab_t162_pine_port as port  # noqa: E402
from scripts.lab_t176_geometry import load, positions  # noqa: E402

NY = ZoneInfo("America/New_York")
WINDOWS = (3, 10, 30)


def signal_rows(bars: Any, mode: str) -> list[dict[str, Any]]:
    features = port.compute_features(bars, mode)
    rows = []
    last_exit = None
    active = None
    for index in range(len(bars)):
        for direction, cross in (
            ("long", features.buy_cross[index]),
            ("short", features.sell_cross[index]),
        ):
            smart = port._smart_ok(bars, features, index, direction)
            risk = port._risk(bars, features, index, direction)[0]
            if cross and smart and risk:
                rows.append(
                    {
                        "index": index,
                        "time": bars[index].ts_utc.isoformat(),
                        "direction": direction,
                        "kind": "first",
                    }
                )
        if last_exit is not None and index - last_exit >= port.REFRESH_BARS:
            for direction in ("long", "short"):
                if (
                    port._smart_ok(bars, features, index, direction)
                    and port._risk(bars, features, index, direction)[0]
                ):
                    rows.append(
                        {
                            "index": index,
                            "time": bars[index].ts_utc.isoformat(),
                            "direction": direction,
                            "kind": "refresh",
                        }
                    )
        # Match the port's single-position lifecycle only to identify refresh state.
        if active is not None:
            bar = bars[index]
            long = active["direction"] == "long"
            stop_hit = bar.low <= active["stop"] if long else bar.high >= active["stop"]
            tp_hit = (
                bar.high >= active["entry"] + active["risk"]
                if long
                else bar.low <= active["entry"] - active["risk"]
            )
            if stop_hit or tp_hit:
                last_exit = index
                active = None
        if active is None:
            candidates = [row for row in rows if row["index"] == index]
            if candidates:
                direction = candidates[0]["direction"]
                risk = port._risk(bars, features, index, direction)[1]
                active = {
                    "direction": direction,
                    "entry": bars[index].close,
                    "risk": risk,
                    "stop": bars[index].close - risk
                    if direction == "long"
                    else bars[index].close + risk,
                }
    return rows


def nearest(
    owner_index: int, direction: str, signals: list[dict[str, Any]], same_only: bool = True
) -> int | None:
    if owner_index is None:
        return None
    candidates = [row["index"] for row in signals if not same_only or row["direction"] == direction]
    if not candidates:
        return None
    return min(candidates, key=lambda value: abs(value - owner_index)) - owner_index


def counts(offsets: list[int | None]) -> dict[str, int]:
    return {
        str(window): sum(value is not None and abs(value) <= window for value in offsets)
        for window in WINDOWS
    }


def observed(
    owner_indices: list[int], directions: list[str], signals: list[dict[str, Any]]
) -> dict[str, Any]:
    same = [
        nearest(index, direction, signals, True)
        for index, direction in zip(owner_indices, directions, strict=True)
    ]
    any_dir = [
        nearest(index, direction, signals, False)
        for index, direction in zip(owner_indices, directions, strict=True)
    ]
    return {
        "same_offsets": same,
        "any_offsets": any_dir,
        "counts": counts(same),
        "median_abs": sorted(abs(value) for value in same if value is not None)[
            len([v for v in same if v is not None]) // 2
        ]
        if any(value is not None for value in same)
        else None,
    }


def pvalue(null_values: list[int], observed_value: int) -> float:
    return (1 + sum(value >= observed_value for value in null_values)) / (len(null_values) + 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rows, entries = load(args.owner_file)
    start = min(row["dt"] for row in entries) - timedelta(days=7)
    end = max(row["dt"] for row in rows) + timedelta(minutes=3)
    bars = tuple(
        port.resample_ohlc(port.read_nas100_minutes(args.lake, start, end), port.TIMEFRAME)
    )
    signals = signal_rows(bars, "closed")
    owner_positions = positions(rows, entries)
    owner_indices = [
        port._bar_at(bars, datetime.fromisoformat(item["entry_time"]))[0]
        for item in owner_positions
    ]
    directions = [item["direction"] for item in owner_positions]
    owner = observed(owner_indices, directions, signals)
    mirror = observed(
        owner_indices,
        ["short" if direction == "long" else "long" for direction in directions],
        signals,
    )
    by_hour: dict[int, list[int]] = {}
    for index, bar in enumerate(bars):
        by_hour.setdefault(bar.ts_utc.astimezone(NY).hour, []).append(index)
    rng = random.Random(177)
    null_counts = {str(window): [] for window in WINDOWS}
    owner_hours = [
        datetime.fromisoformat(item["entry_time"]).astimezone(NY).hour for item in owner_positions
    ]
    for _ in range(2000):
        sampled = [rng.choice(by_hour.get(hour, list(range(len(bars))))) for hour in owner_hours]
        result = observed(sampled, directions, signals)["counts"]
        for window in WINDOWS:
            null_counts[str(window)].append(result[str(window)])
    null_mean = {key: sum(values) / len(values) for key, values in null_counts.items()}
    p_values = {key: pvalue(values, owner["counts"][key]) for key, values in null_counts.items()}
    mirror_p = {key: pvalue(null_counts[key], mirror["counts"][key]) for key in null_counts}
    payload = {
        "task": "T-177",
        "bars": len(bars),
        "signals": signals,
        "signal_count": len(signals),
        "signal_counts_by_day": dict(Counter(row["time"][:10] for row in signals)),
        "owner": owner,
        "mirror": mirror,
        "permutation": {
            "draws": 2000,
            "seed": 177,
            "null_mean": null_mean,
            "p_values": p_values,
            "mirror_p_values": mirror_p,
        },
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "holdout_reads": 0,
            "lake_writes": 0,
            "tuning": False,
            "secrets": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pvals = payload["permutation"]["p_values"]
    verdict = "yes" if min(pvals["10"], pvals["30"]) < 0.01 else "no"
    report = [
        "# T-177 signal coincidence",
        "",
        (
            "Verdict: **Owner entries coincide with port signals better than chance "
            f"{verdict}**: within 3/10/30 bars observed {owner['counts']['3']}/"
            f"{owner['counts']['10']}/{owner['counts']['30']} of {len(owner_indices)}, "
            f"null mean {null_mean['3']:.2f}/{null_mean['10']:.2f}/{null_mean['30']:.2f}, "
            f"p = {pvals['3']:.4f}/{pvals['10']:.4f}/{pvals['30']:.4f}; "
            f"mirror control p = {mirror_p['3']:.4f}/{mirror_p['10']:.4f}/{mirror_p['30']:.4f}; "
            f"median absolute offset {owner['median_abs']} bars."
        ),
        "",
        (
            f"Signals emitted: {len(signals)}; days: "
            f"{dict(Counter(row['time'][:10] for row in signals))}."
        ),
        (
            "A significant result at 10 or 30 bars would support the port signal family with "
            "manual entry delay; no significance supports a different script, version or "
            "settings."
        ),
        "",
        "Replay only: no broker calls, orders, holdout reads, lake writes, tuning or secrets.",
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
