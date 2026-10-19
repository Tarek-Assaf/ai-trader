#!/usr/bin/env python3
"""Test the registered Heikin-Ashi timeframe multipliers without changing the port."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import lab_t162_pine_port as port
from scripts import lab_t172_htf_anchor as t172

TFMULTS = (3, 5, 6, 9, 10, 12, 15, 18, 20, 24, 30, 36, 60)
ANCHORS = ("epoch", "ny_midnight")
MODES = ("closed", "forming")
NY = ZoneInfo("America/New_York")


def bucket_start(ts: datetime, anchor: str, tfmult: int) -> datetime:
    if tfmult <= 0:
        raise ValueError("tfmult must be positive")
    higher_minutes = port.TIMEFRAME * tfmult
    if anchor == "epoch":
        epoch_minute = int(ts.timestamp() // 60)
        return datetime.fromtimestamp((epoch_minute - epoch_minute % higher_minutes) * 60, tz=UTC)
    if anchor != "ny_midnight":
        raise ValueError(f"unknown anchor: {anchor}")
    origin = ts.astimezone(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = ts - origin.astimezone(UTC)
    steps = int(elapsed.total_seconds() // (higher_minutes * 60))
    return origin.astimezone(UTC) + timedelta(minutes=steps * higher_minutes)


def _groups(bars: tuple[Any, ...], anchor: str, tfmult: int) -> list[tuple[datetime, list[int]]]:
    groups: dict[datetime, list[int]] = defaultdict(list)
    for index, bar in enumerate(bars):
        groups[bucket_start(bar.ts_utc, anchor, tfmult)].append(index)
    return sorted(groups.items())


def ha_series(
    bars: tuple[Any, ...], mode: str, anchor: str, tfmult: int
) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    if mode not in MODES:
        raise ValueError("mode must be closed or forming")
    if anchor == "epoch" and tfmult == port.TF_MULT:
        return port._ha_series(bars, mode)
    opens: list[float | None] = [None] * len(bars)
    closes: list[float | None] = [None] * len(bars)
    previous: tuple[float, float] | None = None
    for _, indices in _groups(bars, anchor, tfmult):
        first, last = indices[0], indices[-1]
        if mode == "closed":
            raw_open, raw_close = bars[first].open, bars[last].close
            close = (
                raw_open
                + max(bars[i].high for i in indices)
                + min(bars[i].low for i in indices)
                + raw_close
            ) / 4.0
            value = (
                (raw_open + raw_close) / 2.0
                if previous is None
                else (previous[0] + previous[1]) / 2.0,
                close,
            )
            if previous is not None:
                for i in indices:
                    opens[i], closes[i] = previous
            previous = value
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
                if previous is None
                else (previous[0] + previous[1]) / 2.0,
                close,
            )
        raw_open, raw_close = bars[first].open, bars[last].close
        previous = (
            (raw_open + raw_close) / 2.0 if previous is None else (previous[0] + previous[1]) / 2.0,
            (
                raw_open
                + max(bars[i].high for i in indices)
                + min(bars[i].low for i in indices)
                + raw_close
            )
            / 4.0,
        )
    return tuple(opens), tuple(closes)


def compute_features(bars: tuple[Any, ...], mode: str, anchor: str, tfmult: int) -> port.Features:
    ha_open, ha_close = ha_series(bars, mode, anchor, tfmult)
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


def replay_variant(
    bars: tuple[Any, ...], mode: str, anchor: str, tfmult: int
) -> tuple[tuple[port.PortTrade, ...], port.Features]:
    original = port.compute_features
    port.compute_features = lambda values, selected: compute_features(
        values, selected, anchor, tfmult
    )
    try:
        return port.replay(bars, mode)
    finally:
        port.compute_features = original


def variant_result(
    owners: tuple[Any, ...], bars: tuple[Any, ...], tfmult: int, anchor: str, mode: str
) -> dict[str, Any]:
    trades, features = replay_variant(bars, mode, anchor, tfmult)
    return {
        "tfmult": tfmult,
        "higher_minutes": port.TIMEFRAME * tfmult,
        "anchor": anchor,
        "mode": mode,
        "port_trades": len(trades),
        "matches": t172.matches(owners, trades),
        "cross_timing": t172.cross_timing(owners, bars, features),
        "direction_at_owner_bar": t172.condition_split(owners, bars, features),
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
    variants = [
        variant_result(owners, bars, tfmult, anchor, mode)
        for tfmult in TFMULTS
        for anchor in ANCHORS
        for mode in MODES
    ]
    baseline = next(
        item
        for item in variants
        if item["tfmult"] == 18 and item["anchor"] == "epoch" and item["mode"] == "closed"
    )
    by_tf: list[dict[str, Any]] = []
    for tfmult in TFMULTS:
        choices = [item for item in variants if item["tfmult"] == tfmult]
        best = max(
            choices,
            key=lambda item: (
                item["cross_timing"]["within_2_bars_same_direction"],
                item["matches"]["within_2"],
            ),
        )
        by_tf.append(
            {
                "tfmult": tfmult,
                "best_anchor": best["anchor"],
                "best_mode": best["mode"],
                "within_2_cross": best["cross_timing"]["within_2_bars_same_direction"],
                "within_2_matches": best["matches"]["within_2"],
                "within_3_matches": best["matches"]["within_3"],
            }
        )
    best = max(
        variants,
        key=lambda item: (
            item["cross_timing"]["within_2_bars_same_direction"],
            item["matches"]["within_2"],
        ),
    )
    any_target = any(item["matches"]["within_2"] >= 25 for item in variants)
    payload = {
        "task": "T-173",
        "warmup_start": warm_start.isoformat(),
        "end": end.isoformat(),
        "bars": len(bars),
        "owner_entries": len(owners),
        "baseline_t171_t172": {
            "variant": {"tfmult": 18, "anchor": "epoch", "mode": "closed"},
            "within_2_cross": baseline["cross_timing"]["within_2_bars_same_direction"],
            "matches": baseline["matches"],
        },
        "best_per_tfmult": sorted(
            by_tf, key=lambda item: (-item["within_2_cross"], item["tfmult"])
        ),
        "variants": variants,
        "verdict": {
            "different_tfmult_explains": any_target,
            "best_variant": f"{best['tfmult']}, {best['anchor']}, {best['mode']}",
            "best_within_2_cross": best["cross_timing"]["within_2_bars_same_direction"],
            "best_matches": best["matches"],
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
    caution = (
        " This gain is below 15/31 and is noise across 52 variants; it is not a finding."
        if best["cross_timing"]["within_2_bars_same_direction"] < 15
        else ""
    )
    next_cause = (
        " The next likely cause is Renko/setupType, a different data source "
        "(NQ1! futures versus NSXUSD CFD bars), or a different Pine version; "
        "no further variant was tested."
        if best["cross_timing"]["within_2_bars_same_direction"] <= 8
        else ""
    )
    verdict_line = (
        "**Verdict:** a different tfmult explains it "
        f"{'yes' if payload['verdict']['different_tfmult_explains'] else 'no'}; "
        f"best variant {payload['verdict']['best_variant']}: owner enters "
        f"{best['cross_timing']['within_2_bars_same_direction']} of {len(owners)} "
        "within 2 bars of a same-direction cross; matched "
        f"exact/+/-1/+/-2/+/-3 = {best['matches']['within_0']}/"
        f"{best['matches']['within_1']}/{best['matches']['within_2']}/"
        f"{best['matches']['within_3']} of {len(owners)} (baseline 1/3/4/4); "
        f"any variant >=25 of 31 within +/-2: {'yes' if any_target else 'no'}."
    )
    lines = [
        "# T-173 Heikin-Ashi timeframe multiplier diagnostic",
        "",
        verdict_line,
        "",
        f"The registered grid contains {len(variants)} replay-only variants "
        f"(NSXUSD 3m owner window with full warm-up).{caution}{next_cause}",
        "",
        "## Best result per tfmult",
        "",
        "```text",
        json.dumps(payload["best_per_tfmult"], indent=2),
        "```",
        "",
        "Full per-variant cross timing, matches, direction splits and safety counters "
        "are in the JSON artifact.",
        "",
        "Safety counters are all zero/false; no broker, orders, holdout, lake-write, "
        "production-state, tuning or strategy-search activity.",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
