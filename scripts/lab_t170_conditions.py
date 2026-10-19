#!/usr/bin/env python3
"""Diagnose T-169 owner-bar alignment and one equal-weight VWAP variant."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import lab_t162_pine_port as port

OWNER_DIRECTION = {"long", "short"}
OFFSET_RANGE = range(-10, 11)


def equal_weight_vwap(bars: list[Any] | tuple[Any, ...]) -> tuple[float | None, ...]:
    """Use an equal-weight hlc3 running mean with the port's UTC-day reset."""
    result: list[float | None] = []
    current_day: object = None
    total = 0.0
    count = 0
    for bar in bars:
        if bar.ts_utc.date() != current_day:
            current_day = bar.ts_utc.date()
            total = 0.0
            count = 0
        total += (bar.high + bar.low + bar.close) / 3.0
        count += 1
        result.append(total / count)
    return tuple(result)


def shifted_bar(bars: tuple[Any, ...], index: int | None, offset: int) -> tuple[int | None, Any]:
    if index is None:
        return None, None
    target = index + offset
    if target < 0 or target >= len(bars):
        return None, None
    return target, bars[target]


def direction_at(features: port.Features, index: int | None) -> str:
    if index is None:
        return "none"
    if features.buy_cross[index] and not features.sell_cross[index]:
        return "long"
    if features.sell_cross[index] and not features.buy_cross[index]:
        return "short"
    return "none"


def side_at(value: float | None, reference: float | None) -> str:
    if value is None or reference is None:
        return "none"
    if value > reference:
        return "long"
    if value < reference:
        return "short"
    return "none"


def condition_values(
    bars: tuple[Any, ...], features: port.Features, index: int | None, owner_direction: str
) -> dict[str, Any]:
    if index is None:
        return {
            "direction": "none",
            "ha_state": "none",
            "vwap_side": "none",
            "ema_filter": "none",
            "adx_di": "none",
            "risk": False,
            "cooldown": False,
        }
    bar = bars[index]
    ha_state = side_at(features.ha_close[index], features.ha_open[index])
    ema_filter = side_at(bar.close, features.ema[index])
    adx = features.adx[index]
    plus = features.plus_di[index]
    minus = features.minus_di[index]
    if None in (adx, plus, minus) or float(adx) < 20 or float(plus) == float(minus):
        adx_di = "none"
    elif float(plus) > float(minus):
        adx_di = "long"
    else:
        adx_di = "short"
    vwap_side = side_at(bar.close, features.vwap[index])
    atr = features.atr[index]
    if atr is None or features.vwap[index] is None:
        vwap_pass = False
    else:
        vwap_pass = (
            abs(bar.close - float(features.vwap[index])) / float(atr) <= port.VWAP_DISTANCE_ATR
        )
    risk_pass, _ = port._risk(bars, features, index, owner_direction)
    return {
        "direction": direction_at(features, index),
        "ha_state": ha_state,
        "vwap_side": vwap_side,
        "vwap_distance_pass": bool(vwap_pass),
        "ema_filter": ema_filter,
        "adx_di": adx_di,
        "risk": bool(risk_pass),
        "cooldown": None,
    }


def with_cooldown(values: dict[str, Any], index: int | None, exits: list[int]) -> dict[str, Any]:
    if index is None:
        values["cooldown"] = False
    else:
        values["cooldown"] = not any(0 < index - exit < port.REFRESH_BARS for exit in exits)
    return values


def agree(condition: str, values: dict[str, Any], direction: str) -> bool:
    value = values.get(condition)
    if condition in {"risk", "cooldown"}:
        return value is True
    if condition == "vwap_side":
        return value == direction and values.get("vwap_distance_pass", False)
    return value == direction


def nearest_trade(owner: Any, trades: tuple[port.PortTrade, ...]) -> port.PortTrade | None:
    candidates = [trade for trade in trades if trade.direction == owner.direction]
    return min(
        candidates,
        key=lambda trade: abs((trade.entry_time - owner.entry_time).total_seconds()),
        default=None,
    )


def match_counts(owners: tuple[Any, ...], trades: tuple[port.PortTrade, ...]) -> dict[str, int]:
    values = {f"within_{limit}": 0 for limit in (0, 1, 2, 3)}
    no10 = 0
    for owner in owners:
        nearest = nearest_trade(owner, trades)
        delta = (
            None
            if nearest is None
            else abs((nearest.entry_time - owner.entry_time).total_seconds() / 180.0)
        )
        if delta is None or delta > 10:
            no10 += 1
        else:
            for limit in (0, 1, 2, 3):
                if delta <= limit:
                    values[f"within_{limit}"] += 1
    values["owner_no_same_direction_within_10"] = no10
    return values


def containment_alignment(
    owners: tuple[Any, ...], bars: tuple[Any, ...]
) -> tuple[dict[str, Any], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    counts = {str(offset): 0 for offset in OFFSET_RANGE}
    by_day: dict[str, list[float]] = defaultdict(list)
    for owner in owners:
        base_index, _ = port._bar_at(bars, owner.entry_time)
        basis_values: dict[str, float | None] = {}
        for offset in OFFSET_RANGE:
            _, bar = shifted_bar(bars, base_index, offset)
            basis = None if bar is None else owner.entry_price - bar.close
            basis_values[str(offset)] = basis
        finite_basis = [value for value in basis_values.values() if value is not None]
        overall = median(finite_basis) if finite_basis else None
        by_day[owner.entry_time.astimezone(port.OWNER_ZONE).date().isoformat()].extend(finite_basis)
        rows.append(
            {
                "owner_time_ny": owner.entry_time.astimezone(port.OWNER_ZONE).isoformat(),
                "owner_time_utc": owner.entry_time.isoformat(),
                "direction": owner.direction,
                "owner_price": owner.entry_price,
                "bar_index": base_index,
                "basis_owner_minus_bar_close": basis_values,
                "median_basis_for_owner": overall,
            }
        )
    overall_basis_values = [
        value
        for row in rows
        for value in row["basis_owner_minus_bar_close"].values()
        if value is not None
    ]
    overall_basis = median(overall_basis_values) if overall_basis_values else None
    for row in rows:
        for offset in OFFSET_RANGE:
            _, bar = shifted_bar(bars, row["bar_index"], offset)
            if bar is None or overall_basis is None:
                continue
            adjusted_owner = row["owner_price"] - overall_basis
            if bar.low <= adjusted_owner <= bar.high:
                counts[str(offset)] += 1
    day_medians = {
        day: {"count": len(values), "median_basis": median(values) if values else None}
        for day, values in sorted(by_day.items())
    }
    return (
        {
            "overall_median_basis_owner_minus_bar_close": overall_basis,
            "per_ny_day_median_basis": day_medians,
            "rows": rows,
        },
        counts,
    )


def condition_alignment(
    owners: tuple[Any, ...],
    bars: tuple[Any, ...],
    features: port.Features,
    trades: tuple[port.PortTrade, ...],
) -> dict[str, Any]:
    conditions = ["direction", "ha_state", "vwap_side", "ema_filter", "adx_di", "risk", "cooldown"]
    rows: list[dict[str, Any]] = []
    counts = {str(offset): {name: 0 for name in conditions} for offset in (-1, 0, 1)}
    exits = [trade.exit_index for trade in trades if trade.exit_index is not None]
    for owner in owners:
        base_index, _ = port._bar_at(bars, owner.entry_time)
        offset_values: dict[str, Any] = {}
        for offset in (-1, 0, 1):
            index, _ = shifted_bar(bars, base_index, offset)
            values = with_cooldown(
                condition_values(bars, features, index, owner.direction), index, exits
            )
            offset_values[str(offset)] = values
            for name in conditions:
                if agree(name, values, owner.direction):
                    counts[str(offset)][name] += 1
        rows.append(
            {
                "owner_time_utc": owner.entry_time.isoformat(),
                "owner_time_ny": owner.entry_time.astimezone(port.OWNER_ZONE).isoformat(),
                "direction": owner.direction,
                "bar_index": base_index,
                "offsets": offset_values,
            }
        )
    return {"counts_agree_with_owner_direction": counts, "rows": rows}


def run_equal_vwap(bars: tuple[Any, ...], owners: tuple[Any, ...]) -> dict[str, Any]:
    original = port._vwap
    port._vwap = equal_weight_vwap
    try:
        trades, _ = port.replay(bars, "closed")
    finally:
        port._vwap = original
    return {
        "vwap": "equal_weight_hlc3",
        "trades": len(trades),
        "matches": match_counts(owners, trades),
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
    containment, containment_counts = containment_alignment(owners, bars)
    conditions = condition_alignment(owners, bars, features, trades)
    equal_vwap = run_equal_vwap(bars, owners)
    payload = {
        "task": "T-170",
        "warmup_start": warm_start.isoformat(),
        "end": end.isoformat(),
        "bars": len(bars),
        "owner_entries": len(owners),
        "baseline": {
            "port_closed_trades": len(trades),
            "t169_matches": match_counts(owners, trades),
        },
        "clock_alignment": {
            "containment_counts_after_overall_median_basis_subtraction": containment_counts,
            **containment,
        },
        "condition_alignment": conditions,
        "equal_weight_vwap_variant": equal_vwap,
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
    peak_offset, peak_count = max(containment_counts.items(), key=lambda item: item[1])
    lowest = min(
        conditions["counts_agree_with_owner_direction"]["0"].items(), key=lambda item: item[1]
    )
    eq3 = equal_vwap["matches"]["within_3"]
    lines = [
        "# T-170 owner-bar clock and condition diagnosis",
        "",
        f"**Verdict:** clock aligned {'yes' if peak_offset == '0' else 'no'} "
        f"(peak offset {peak_offset}, count {peak_count} of {len(owners)}); "
        f"lowest-agreement condition {lowest[0]} ({lowest[1]} of {len(owners)}); "
        f"equal-weight VWAP gives {eq3} of {len(owners)} within +/-3 "
        f"(baseline {match_counts(owners, trades)['within_3']}).",
        "",
        "The diagnostic is replay-only. A run reaches the 25-entry target only if the "
        "verdict says so; otherwise the most likely cause is the lowest-agreement "
        "condition or a moving "
        "price basis.",
        "",
        "## Clock containment counts",
        "",
        "After subtracting the overall median owner-minus-port close basis, counts by "
        "bar offset are:",
        "",
        "```text",
        json.dumps(containment_counts, sort_keys=True),
        "```",
        "",
        f"Overall median basis: {containment['overall_median_basis_owner_minus_bar_close']:.2f}.",
        "Per-entry basis lists and per-New-York-day medians are in the JSON artifact.",
        "",
        "## Per-condition agreement",
        "",
        "json",
        json.dumps(conditions["counts_agree_with_owner_direction"], indent=2),
        "",
        "## Equal-weight VWAP",
        "",
        json.dumps(equal_vwap, indent=2),
        "",
        "Safety counters are all zero/false; no broker, orders, holdout, lake-write, "
        "production-state, tuning or strategy-search activity.",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
