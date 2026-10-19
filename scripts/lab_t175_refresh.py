#!/usr/bin/env python3
"""Diagnostic replay of owner-exit-driven Pine refresh entries for T-175."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import lab_t162_pine_port as port  # noqa: E402

OFFSETS = tuple(range(-2, 3))
CONDITIONS = ("direction", "ema", "adx_di", "vwap_side", "location", "risk", "cooldown")


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def classify(owners: Any, exits: Any, bars: Any) -> list[dict[str, Any]]:
    exit_rows = [(item, port._bar_at(bars, item.exit_time)[0]) for item in exits]
    states = []
    for owner in owners:
        index = port._bar_at(bars, owner.entry_time)[0]
        recent = []
        opened = False
        if index is not None:
            for item, exit_index in exit_rows:
                if item.direction != owner.direction or exit_index is None:
                    continue
                if 0 <= index - exit_index <= 20:
                    recent.append((item, exit_index))
                entry_index = port._bar_at(bars, item.entry_time)[0]
                if entry_index is not None and entry_index <= index < exit_index:
                    opened = True
        previous = min((index - value for _, value in recent), default=None)
        states.append(
            {
                "owner": _iso(owner.entry_time),
                "direction": owner.direction,
                "bar_index": index,
                "class": "refresh" if recent or opened else "first",
                "same_direction_exit_within_20": bool(recent),
                "open_owner_position_same_direction": opened,
                "bars_since_same_direction_exit": previous,
            }
        )
    return states


def condition_values(
    bars: Any, features: Any, index: int | None, direction: str, kind: str, exits: Any
) -> dict[str, bool]:
    values = {name: False for name in CONDITIONS}
    if index is None or index < 0 or index >= len(bars):
        return values
    bar = bars[index]
    ema, atr, adx, plus, minus, vwap = (
        features.ema[index],
        features.atr[index],
        features.adx[index],
        features.plus_di[index],
        features.minus_di[index],
        features.vwap[index],
    )
    if kind == "refresh":
        values["direction"] = True
    elif direction == "long":
        values["direction"] = features.buy_cross[index]
    else:
        values["direction"] = features.sell_cross[index]
    if None not in (ema, atr, adx, plus, minus, vwap):
        values["ema"] = bar.close > float(ema) if direction == "long" else bar.close < float(ema)
        values["adx_di"] = float(adx) >= 20 and (
            float(plus) > float(minus) if direction == "long" else float(minus) > float(plus)
        )
        values["vwap_side"] = (
            bar.close > float(vwap) if direction == "long" else bar.close < float(vwap)
        )
        values["location"] = (
            abs(bar.close - float(vwap)) / float(atr) <= port.VWAP_DISTANCE_ATR
            if float(atr) > 0
            else False
        )
    values["risk"] = port._risk(bars, features, index, direction)[0]
    exit_indices = [port._bar_at(bars, item.exit_time)[0] for item in exits]
    values["cooldown"] = not any(
        value is not None and 0 < index - value < port.REFRESH_BARS for value in exit_indices
    )
    return values


def cross_timing(states: Any, bars: Any, features: Any) -> dict[str, Any]:
    ages = []
    histogram: Counter[str] = Counter()
    for state in states:
        if state["class"] != "first" or state["bar_index"] is None:
            continue
        index = int(state["bar_index"])
        cross = features.buy_cross if state["direction"] == "long" else features.sell_cross
        age = next((index - j for j in range(index, max(-1, index - 61), -1) if cross[j]), None)
        if age is None:
            histogram["none"] += 1
        elif age == 0:
            histogram["0"] += 1
            ages.append(age)
        elif age <= 2:
            histogram["1-2"] += 1
            ages.append(age)
        elif age <= 5:
            histogram["3-5"] += 1
            ages.append(age)
        elif age <= 10:
            histogram["6-10"] += 1
            ages.append(age)
        elif age <= 20:
            histogram["11-20"] += 1
            ages.append(age)
        else:
            histogram["21-60"] += 1
            ages.append(age)
    median = sorted(ages)[len(ages) // 2] if ages else None
    return {
        "histogram": dict(sorted(histogram.items())),
        "within_2": histogram["0"] + histogram["1-2"],
        "median_age": median,
    }


def evaluate(states: Any, bars: Any, mode: str, exits: Any) -> dict[str, Any]:
    features = port.compute_features(bars, mode)
    rows = []
    class_counts: Counter[str] = Counter()
    reproduced_counts: Counter[str] = Counter()
    condition_counts = {kind: Counter() for kind in ("first", "refresh")}
    for state in states:
        kind = state["class"]
        class_counts[kind] += 1
        offset_values = {}
        for offset in OFFSETS:
            index = state["bar_index"] + offset if state["bar_index"] is not None else None
            values = condition_values(bars, features, index, state["direction"], kind, exits)
            offset_values[str(offset)] = values
            for name, passed in values.items():
                condition_counts[kind][name] += int(passed)
        reproduced = any(all(offset_values[str(offset)].values()) for offset in OFFSETS)
        if reproduced:
            reproduced_counts[kind] += 1
        rows.append({**state, "offsets": offset_values, "reproduced": reproduced})
    lowest = {}
    for kind in ("first", "refresh"):
        lowest[kind] = (
            list(min(condition_counts[kind].items(), key=lambda pair: pair[1]))
            if class_counts[kind]
            else None
        )
    return {
        "mode": mode,
        "owner_count": len(states),
        "class_counts": dict(class_counts),
        "reproduced": {
            "first": reproduced_counts["first"],
            "refresh": reproduced_counts["refresh"],
            "overall": sum(reproduced_counts.values()),
        },
        "condition_passes": {kind: dict(condition_counts[kind]) for kind in ("first", "refresh")},
        "lowest_passing_condition": lowest,
        "first_cross_timing": cross_timing(states, bars, features),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    owners = port.read_owner_entries(args.owner_file)
    exits = port.read_owner_exits(args.owner_file)
    start = min(item.entry_time for item in owners) - timedelta(days=7)
    end = datetime(2026, 10, 17, tzinfo=UTC)
    minutes = port.read_nas100_minutes(args.lake, start, end)
    bars = tuple(port.resample_ohlc(minutes, port.TIMEFRAME))
    states = classify(owners, exits, bars)
    modes = {mode: evaluate(states, bars, mode, exits) for mode in ("closed", "forming")}
    best = max(modes.values(), key=lambda item: item["reproduced"]["overall"])
    count = best["reproduced"]["overall"]
    verdict = "reproduced" if count >= 25 else "not_reproduced"
    payload = {
        "task": "T-175",
        "warmup_start": start,
        "end": end,
        "bars": len(bars),
        "owner_entries": len(owners),
        "owner_exits": len(exits),
        "owner_classification": states,
        "modes": modes,
        "verdict": verdict,
        "best_mode": best["mode"],
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
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_iso) + "\n", encoding="utf-8"
    )
    lines = [
        "# T-175 owner-exit-driven refresh diagnostic",
        "",
        f"Verdict: **{verdict}** ({count}/{len(owners)} owner entries reproduced; threshold 25).",
        f"Best mode: {best['mode']}.",
        "",
        "## Counts",
        "",
        f"Owner entries: {len(owners)}; owner exits: {len(exits)}; chart bars: {len(bars)}.",
    ]
    for mode, result in modes.items():
        lines.append(
            f"- {mode}: {result['reproduced']['overall']}/{len(owners)} overall; first={result['reproduced']['first']}; refresh={result['reproduced']['refresh']}"  # noqa: E501
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "First entries require a same-direction HA cross; refresh entries use the Pine refresh rule after owner exits. Every condition is reported at offsets -2..+2. Results below 15 should be treated as noise; if <=8, next causes are NQ1 futures versus NSXUSD CFD or Pine version differences.",  # noqa: E501
        "",
        "## Safety",
        "",
        "Replay only; no broker calls, orders, holdout reads, lake writes, tuning, or secrets.",
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
