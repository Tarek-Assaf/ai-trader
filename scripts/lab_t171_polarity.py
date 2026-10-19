#!/usr/bin/env python3
"""Separate polarity from silence in the T-170 owner-bar conditions."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import lab_t162_pine_port as port
from scripts import lab_t170_conditions as t170

CONDITIONS = ("direction", "ha_state", "vwap_side", "ema_filter", "adx_di")
OFFSETS = (-1, 0, 1)
LOOKBACK = 60


def polarity(value: Any, owner_direction: str) -> str:
    """Classify a categorical condition relative to the owner direction."""
    if value == owner_direction:
        return "same"
    if value in {"long", "short"}:
        return "opposite"
    return "none"


def three_way_split(
    owners: tuple[Any, ...],
    bars: tuple[Any, ...],
    features: port.Features,
) -> dict[str, Any]:
    counts = {
        str(offset): {
            condition: {label: 0 for label in ("same", "opposite", "none")}
            for condition in CONDITIONS
        }
        for offset in OFFSETS
    }
    rows: list[dict[str, Any]] = []
    for owner in owners:
        base_index, _ = port._bar_at(bars, owner.entry_time)
        row: dict[str, Any] = {
            "owner_time_utc": owner.entry_time.isoformat(),
            "owner_time_ny": owner.entry_time.astimezone(port.OWNER_ZONE).isoformat(),
            "direction": owner.direction,
            "bar_index": base_index,
            "offsets": {},
        }
        for offset in OFFSETS:
            index, _ = t170.shifted_bar(bars, base_index, offset)
            values = t170.condition_values(bars, features, index, owner.direction)
            classified = {
                condition: polarity(values[condition], owner.direction) for condition in CONDITIONS
            }
            row["offsets"][str(offset)] = classified
            for condition, label in classified.items():
                counts[str(offset)][condition][label] += 1
        rows.append(row)
    return {"counts": counts, "rows": rows}


def _cross_index(
    features: port.Features, index: int | None, direction: str, same: bool
) -> int | None:
    if index is None:
        return None
    cross = features.buy_cross if (direction == "long") == same else features.sell_cross
    first = max(0, index - LOOKBACK)
    for candidate in range(index, first - 1, -1):
        if cross[candidate]:
            return candidate
    return None


def cross_timing(
    owners: tuple[Any, ...], bars: tuple[Any, ...], features: port.Features
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    same_values: list[int] = []
    histogram = Counter(
        {bucket: 0 for bucket in ("0", "1-2", "3-5", "6-10", "11-20", "21-60", "none")}
    )
    within_2 = 0
    for owner in owners:
        index, _ = port._bar_at(bars, owner.entry_time)
        same_index = _cross_index(features, index, owner.direction, True)
        opposite_index = _cross_index(features, index, owner.direction, False)
        same_distance = None if same_index is None or index is None else index - same_index
        opposite_distance = (
            None if opposite_index is None or index is None else index - opposite_index
        )
        if same_distance is None:
            bucket = "none"
        elif same_distance == 0:
            bucket = "0"
            same_values.append(same_distance)
        elif same_distance <= 2:
            bucket = "1-2"
            same_values.append(same_distance)
        elif same_distance <= 5:
            bucket = "3-5"
            same_values.append(same_distance)
        elif same_distance <= 10:
            bucket = "6-10"
            same_values.append(same_distance)
        elif same_distance <= LOOKBACK:
            bucket = "11-20" if same_distance <= 20 else "21-60"
            same_values.append(same_distance)
        else:
            bucket = "none"
        histogram[bucket] += 1
        if same_distance is not None and same_distance <= 2:
            within_2 += 1
        rows.append(
            {
                "owner_time_utc": owner.entry_time.isoformat(),
                "owner_time_ny": owner.entry_time.astimezone(port.OWNER_ZONE).isoformat(),
                "direction": owner.direction,
                "bar_index": index,
                "same_direction_cross_bars_ago": same_distance,
                "opposite_direction_cross_bars_ago": opposite_distance,
            }
        )
    return {
        "lookback_bars": LOOKBACK,
        "within_2_bars_same_direction": within_2,
        "median_same_direction_bars_ago": median(same_values) if same_values else None,
        "same_direction_histogram": dict(histogram),
        "rows": rows,
    }


def _flipped(direction: str) -> str:
    return "short" if direction == "long" else "long"


def mirror_matches(owners: tuple[Any, ...], trades: tuple[port.PortTrade, ...]) -> dict[str, Any]:
    """Re-score unchanged entry bars with each port trade direction flipped."""
    exact = {"baseline": 0, "mirror": 0}
    within = {str(limit): {"baseline": 0, "mirror": 0} for limit in (1, 2, 3)}
    rows: list[dict[str, Any]] = []
    for owner in owners:
        baseline_candidates = [trade for trade in trades if trade.direction == owner.direction]
        mirror_candidates = [
            trade for trade in trades if _flipped(trade.direction) == owner.direction
        ]
        baseline = min(
            baseline_candidates,
            key=lambda trade: abs((trade.entry_time - owner.entry_time).total_seconds()),
            default=None,
        )
        mirror = min(
            mirror_candidates,
            key=lambda trade: abs((trade.entry_time - owner.entry_time).total_seconds()),
            default=None,
        )
        baseline_delta = (
            None
            if baseline is None
            else (baseline.entry_time - owner.entry_time).total_seconds() / (port.TIMEFRAME * 60)
        )
        mirror_delta = (
            None
            if mirror is None
            else (mirror.entry_time - owner.entry_time).total_seconds() / (port.TIMEFRAME * 60)
        )
        for label, delta in (("baseline", baseline_delta), ("mirror", mirror_delta)):
            if delta is not None and abs(delta) <= 0.01:
                exact[label] += 1
            for limit in (1, 2, 3):
                if delta is not None and abs(delta) <= limit:
                    within[str(limit)][label] += 1
        rows.append(
            {
                "owner_time_utc": owner.entry_time.isoformat(),
                "direction": owner.direction,
                "baseline_port_direction": baseline.direction if baseline else None,
                "baseline_signed_delta_bars": baseline_delta,
                "mirror_port_direction": _flipped(mirror.direction) if mirror else None,
                "mirror_signed_delta_bars": mirror_delta,
            }
        )
    return {
        "baseline": {
            "exact": exact["baseline"],
            **{f"within_{k}": v["baseline"] for k, v in within.items()},
        },
        "mirror": {
            "exact": exact["mirror"],
            **{f"within_{k}": v["mirror"] for k, v in within.items()},
        },
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    owners = port.read_owner_entries(args.owner_file)
    warm_start = min(owner.entry_time for owner in owners) - timedelta(days=7)
    end = datetime(2026, 10, 17, tzinfo=UTC)
    minutes = port.read_nas100_minutes(args.lake, warm_start, end)
    bars = tuple(item for item in port.resample_ohlc(minutes, port.TIMEFRAME) if item.complete)
    if not bars:
        raise RuntimeError("NAS100 3m data is missing")
    trades, features = port.replay(bars, "closed")
    split = three_way_split(owners, bars, features)
    timing = cross_timing(owners, bars, features)
    mirror = mirror_matches(owners, trades)
    polarity_conditions = [
        condition for condition in CONDITIONS if split["counts"]["0"][condition]["opposite"] >= 21
    ]
    silent_conditions = [
        condition for condition in CONDITIONS if split["counts"]["0"][condition]["none"] >= 21
    ]
    payload = {
        "task": "T-171",
        "warmup_start": warm_start.isoformat(),
        "end": end.isoformat(),
        "bars": len(bars),
        "owner_entries": len(owners),
        "baseline": {
            "port_closed_trades": len(trades),
            "t169_matches": t170.match_counts(owners, trades),
        },
        "three_way_condition_split": split,
        "cross_timing": timing,
        "mirror_rescore": mirror,
        "verdict": {
            "polarity_problem": bool(polarity_conditions),
            "polarity_conditions_opposite_at_least_21": polarity_conditions,
            "silent_signal_problem": bool(silent_conditions),
            "silent_conditions_none_at_least_21": silent_conditions,
            "owner_within_2_bars_same_direction_cross": timing["within_2_bars_same_direction"],
            "mirror_within_3": mirror["mirror"]["within_3"],
            "baseline_within_3": mirror["baseline"]["within_3"],
        },
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "sealed_holdout_read": False,
            "lake_writes": 0,
            "production_state_writes": 0,
            "tuning": False,
            "strategy_search": False,
        },
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    opposite_text = ", ".join(polarity_conditions) or "none"
    silent_text = ", ".join(silent_conditions) or "none"
    reached_target = (
        timing["within_2_bars_same_direction"] >= 25 or mirror["mirror"]["within_3"] >= 25
    )
    cause = (
        "The 25-entry target is reached by at least one requested diagnostic."
        if reached_target
        else (
            "The target is not met; the numbers point to signal or entry-rule divergence "
            "rather than a broad polarity inversion."
        )
    )
    lines = [
        "# T-171 condition polarity and cross timing",
        "",
        f"**Verdict:** polarity problem {'yes' if polarity_conditions else 'no'} "
        f"(conditions with opposite >= 21: {opposite_text}); silent-signal problem "
        f"{'yes' if silent_conditions else 'no'} (conditions with none >= 21: {silent_text}); "
        f"owner enters {timing['within_2_bars_same_direction']} of {len(owners)} within 2 bars "
        f"of a same-direction cross; mirror gives {mirror['mirror']['within_3']} of {len(owners)} "
        f"within +/-3 (baseline {mirror['baseline']['within_3']}). {cause}",
        "",
        "## Three-way split at owner bars",
        "",
        json.dumps(split["counts"], indent=2),
        "",
        "## Cross timing",
        "",
        f"Median same-direction cross age: {timing['median_same_direction_bars_ago']}; "
        f"histogram: {json.dumps(timing['same_direction_histogram'], sort_keys=True)}.",
        "Per-entry same and opposite cross ages are in the JSON artifact.",
        "",
        "## Mirror re-score",
        "",
        json.dumps({"baseline": mirror["baseline"], "mirror": mirror["mirror"]}, indent=2),
        "",
        "Safety counters are all zero/false; no broker, orders, holdout, lake-write, "
        "production-state, tuning or strategy-search activity.",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
