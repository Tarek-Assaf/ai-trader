#!/usr/bin/env python3
"""Test registered 54-minute Heikin-Ashi bucket anchors without changing the port."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import lab_t162_pine_port as port
from scripts import lab_t170_conditions as t170

ANCHORS = ("epoch", "cme_session", "ny_midnight", "utc_midnight")
MODES = ("closed", "forming")
LOOKBACK = 60
NY = ZoneInfo("America/New_York")


def _bucket_start(ts: datetime, anchor: str) -> datetime:
    """Return the registered anchor's 54-minute bucket start."""
    if anchor == "epoch":
        epoch_minute = int(ts.timestamp() // 60)
        return datetime.fromtimestamp(
            (epoch_minute - epoch_minute % port.HIGHER_MINUTES) * 60, tz=UTC
        )
    if anchor == "utc_midnight":
        origin = ts.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    elif anchor == "ny_midnight":
        origin = ts.astimezone(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    elif anchor == "cme_session":
        local = ts.astimezone(NY)
        session_date = local.date() if local.hour >= 18 else local.date() - timedelta(days=1)
        origin = datetime(session_date.year, session_date.month, session_date.day, 18, tzinfo=NY)
        if local.hour == 17:
            return origin + timedelta(hours=23)
    else:
        raise ValueError(f"unknown anchor: {anchor}")
    elapsed = ts - origin.astimezone(UTC)
    steps = int(elapsed.total_seconds() // (port.HIGHER_MINUTES * 60))
    return origin.astimezone(UTC) + timedelta(minutes=steps * port.HIGHER_MINUTES)


def _groups(bars: tuple[Any, ...], anchor: str) -> list[tuple[datetime, list[int]]]:
    groups: dict[datetime, list[int]] = defaultdict(list)
    for index, bar in enumerate(bars):
        groups[_bucket_start(bar.ts_utc, anchor)].append(index)
    return sorted(groups.items())


def _ha_series_anchor(
    bars: tuple[Any, ...], mode: str, anchor: str
) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    if mode not in MODES:
        raise ValueError("mode must be closed or forming")
    if anchor == "epoch":
        return port._ha_series(bars, mode)
    opens: list[float | None] = [None] * len(bars)
    closes: list[float | None] = [None] * len(bars)
    previous_complete: tuple[float, float] | None = None
    for _, indices in _groups(bars, anchor):
        if not indices:
            continue
        if mode == "closed":
            first, last = indices[0], indices[-1]
            raw_open, raw_close = bars[first].open, bars[last].close
            close = (
                raw_open
                + max(bars[i].high for i in indices)
                + min(bars[i].low for i in indices)
                + raw_close
            ) / 4.0
            value = (
                (raw_open + raw_close) / 2.0
                if previous_complete is None
                else (previous_complete[0] + previous_complete[1]) / 2.0,
                close,
            )
            if previous_complete is not None:
                for i in indices:
                    opens[i], closes[i] = previous_complete
            previous_complete = value
            continue
        for position, i in enumerate(indices):
            prefix = indices[: position + 1]
            raw_open, raw_close = bars[prefix[0]].open, bars[prefix[-1]].close
            close = (
                raw_open
                + max(bars[j].high for j in prefix)
                + min(bars[j].low for j in prefix)
                + raw_close
            ) / 4.0
            opens[i], closes[i] = (
                (raw_open + raw_close) / 2.0
                if previous_complete is None
                else (previous_complete[0] + previous_complete[1]) / 2.0,
                close,
            )
        first, last = indices[0], indices[-1]
        raw_open, raw_close = bars[first].open, bars[last].close
        previous_complete = (
            (raw_open + raw_close) / 2.0
            if previous_complete is None
            else (previous_complete[0] + previous_complete[1]) / 2.0,
            (
                raw_open
                + max(bars[i].high for i in indices)
                + min(bars[i].low for i in indices)
                + raw_close
            )
            / 4.0,
        )
    return tuple(opens), tuple(closes)


def compute_features_anchor(bars: tuple[Any, ...], mode: str, anchor: str) -> port.Features:
    ha_open, ha_close = _ha_series_anchor(bars, mode, anchor)
    buy, sell = port._crosses(ha_open, ha_close)
    return port.Features(
        port._ema([bar.close for bar in bars], port.EMA_PERIOD),
        port._atr(bars),
        *port._dmi(bars),
        port._vwap(bars),
        ha_open,
        ha_close,
        buy,
        sell,
    )


def replay_anchor(
    bars: tuple[Any, ...], mode: str, anchor: str
) -> tuple[tuple[Any, ...], port.Features]:
    original = port.compute_features
    port.compute_features = lambda values, selected: compute_features_anchor(
        values, selected, anchor
    )
    try:
        return port.replay(bars, mode)
    finally:
        port.compute_features = original


def cross_timing(
    owners: tuple[Any, ...], bars: tuple[Any, ...], features: port.Features
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    histogram = Counter(
        {bucket: 0 for bucket in ("0", "1-2", "3-5", "6-10", "11-20", "21-60", "none")}
    )
    ages: list[int] = []
    within_2 = 0
    for owner in owners:
        index, _ = port._bar_at(bars, owner.entry_time)
        cross = features.buy_cross if owner.direction == "long" else features.sell_cross
        age = None
        if index is not None:
            for candidate in range(index, max(-1, index - LOOKBACK - 1), -1):
                if cross[candidate]:
                    age = index - candidate
                    break
        if age is None:
            bucket = "none"
        elif age == 0:
            bucket = "0"
        elif age <= 2:
            bucket = "1-2"
        elif age <= 5:
            bucket = "3-5"
        elif age <= 10:
            bucket = "6-10"
        elif age <= 20:
            bucket = "11-20"
        else:
            bucket = "21-60"
        histogram[bucket] += 1
        if age is not None:
            ages.append(age)
        if age is not None and age <= 2:
            within_2 += 1
        rows.append(
            {
                "owner_time_utc": owner.entry_time.isoformat(),
                "direction": owner.direction,
                "bar_index": index,
                "same_direction_cross_bars_ago": age,
            }
        )
    return {
        "within_2_bars_same_direction": within_2,
        "median_same_direction_bars_ago": median(ages) if ages else None,
        "same_direction_histogram": dict(histogram),
        "rows": rows,
    }


def condition_split(
    owners: tuple[Any, ...], bars: tuple[Any, ...], features: port.Features
) -> dict[str, Any]:
    counts = {
        label: {"same": 0, "opposite": 0, "none": 0}
        for label in ("direction", "ha_state", "vwap_side", "ema_filter", "adx_di")
    }
    for owner in owners:
        index, _ = port._bar_at(bars, owner.entry_time)
        values = t170.condition_values(bars, features, index, owner.direction)
        for condition in counts:
            value = values[condition]
            label = (
                "same"
                if value == owner.direction
                else "opposite"
                if value in {"long", "short"}
                else "none"
            )
            counts[condition][label] += 1
    return counts


def matches(owners: tuple[Any, ...], trades: tuple[port.PortTrade, ...]) -> dict[str, int]:
    out = {f"within_{limit}": 0 for limit in (0, 1, 2, 3)}
    for owner in owners:
        candidates = [trade for trade in trades if trade.direction == owner.direction]
        nearest = min(
            candidates,
            key=lambda trade: abs((trade.entry_time - owner.entry_time).total_seconds()),
            default=None,
        )
        if nearest is None:
            continue
        delta = abs((nearest.entry_time - owner.entry_time).total_seconds() / (port.TIMEFRAME * 60))
        for limit in (0, 1, 2, 3):
            if delta <= limit:
                out[f"within_{limit}"] += 1
    return out


def run_variant(
    owners: tuple[Any, ...], bars: tuple[Any, ...], anchor: str, mode: str
) -> dict[str, Any]:
    trades, features = replay_anchor(bars, mode, anchor)
    return {
        "anchor": anchor,
        "mode": mode,
        "port_closed_trades": len(trades),
        "matches": matches(owners, trades),
        "cross_timing": cross_timing(owners, bars, features),
        "direction_at_owner_bar": condition_split(owners, bars, features),
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
    variants = [run_variant(owners, bars, anchor, mode) for anchor in ANCHORS for mode in MODES]
    baseline = next(v for v in variants if v["anchor"] == "epoch" and v["mode"] == "closed")
    best = max(
        variants,
        key=lambda item: (
            item["cross_timing"]["within_2_bars_same_direction"],
            item["matches"]["within_2"],
        ),
    )
    any_target = any(item["matches"]["within_2"] >= 25 for item in variants)
    payload = {
        "task": "T-172",
        "warmup_start": warm_start.isoformat(),
        "end": end.isoformat(),
        "bars": len(bars),
        "owner_entries": len(owners),
        "baseline_t171": {
            "within_2_cross": baseline["cross_timing"]["within_2_bars_same_direction"],
            "matches": baseline["matches"],
        },
        "variants": variants,
        "verdict": {
            "anchor_hypothesis": best["cross_timing"]["within_2_bars_same_direction"]
            > baseline["cross_timing"]["within_2_bars_same_direction"],
            "best_variant": f"{best['anchor']}, {best['mode']}",
            "best_within_2_cross": best["cross_timing"]["within_2_bars_same_direction"],
            "any_variant_at_least_25_within_2": any_target,
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
    cause = (
        "the registered anchors do not explain the gap; inspect the Renko/setupType path, "
        "originalFilter or owner inputs"
        if not any_target
        else "the best anchor should be queued for the historical rerun"
    )
    verdict_line = (
        f"**Verdict:** anchor hypothesis "
        f"{'yes' if payload['verdict']['anchor_hypothesis'] else 'no'}; "
        f"best variant {payload['verdict']['best_variant']}: owner enters "
        f"{best['cross_timing']['within_2_bars_same_direction']} of {len(owners)} "
        "within 2 bars of a same-direction cross; entries matched "
        f"exact/+/-1/+/-2/+/-3 = {best['matches']['within_0']}/"
        f"{best['matches']['within_1']}/{best['matches']['within_2']}/"
        f"{best['matches']['within_3']} of {len(owners)} (baseline 1/3/4/4); "
        f"any variant >=25 of 31: {'yes' if any_target else 'no'}."
    )
    lines = [
        "# T-172 HTF Heikin-Ashi anchor diagnostic",
        "",
        verdict_line,
        "",
        "The eight registered variants are replay-only. If no variant improves cross timing "
        f"above 8 of 31, the next likely cause is {cause}; no unregistered variant was tested.",
        "",
        "## Variant results",
        "",
        "```json",
        json.dumps(
            [{k: v for k, v in item.items() if k != "direction_at_owner_bar"} for item in variants],
            indent=2,
        ),
        "```",
        "",
        "Full per-entry timing, condition counts and safety counters are in the JSON artifact.",
        "",
        "Safety counters are all zero/false; no broker, orders, holdout, lake-write, "
        "production-state, tuning or strategy-search activity.",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
