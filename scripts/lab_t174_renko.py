#!/usr/bin/env python3
"""Replay the registered Renko setup grid without touching the Pine port."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import lab_t162_pine_port as port
from scripts import lab_t172_htf_anchor as t172

ATR_TIMEFRAMES = ("54m", "3m")
ANCHORS = ("epoch", "ny_midnight")
MODES = ("closed", "forming")
NY = ZoneInfo("America/New_York")
HIGHER_MINUTES = 54


def bucket_start(ts: datetime, anchor: str) -> datetime:
    if anchor == "epoch":
        minute = int(ts.timestamp() // 60)
        return datetime.fromtimestamp((minute - minute % HIGHER_MINUTES) * 60, tz=UTC)
    if anchor != "ny_midnight":
        raise ValueError(f"unknown anchor: {anchor}")
    origin = ts.astimezone(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = ts - origin.astimezone(UTC)
    steps = int(elapsed.total_seconds() // (HIGHER_MINUTES * 60))
    return origin.astimezone(UTC) + timedelta(minutes=steps * HIGHER_MINUTES)


def _groups(bars: Sequence[Any], anchor: str) -> list[tuple[datetime, list[int]]]:
    groups: dict[datetime, list[int]] = defaultdict(list)
    for index, bar in enumerate(bars):
        groups[bucket_start(bar.ts_utc, anchor)].append(index)
    return sorted(groups.items())


def aggregate_54m(bars: tuple[Any, ...], anchor: str) -> tuple[Any, ...]:
    """Build complete 54-minute source bars on the requested session grid."""
    result: list[Any] = []
    for start, indices in _groups(bars, anchor):
        if len(indices) != HIGHER_MINUTES // port.TIMEFRAME:
            continue
        if any(
            bars[a].ts_utc + timedelta(minutes=port.TIMEFRAME) != bars[b].ts_utc
            for a, b in zip(indices[:-1], indices[1:], strict=True)
        ):
            continue
        first, last = indices[0], indices[-1]
        result.append(
            port.Candle(
                ts_utc=start,
                open=bars[first].open,
                high=max(bars[i].high for i in indices),
                low=min(bars[i].low for i in indices),
                close=bars[last].close,
                volume=sum(bars[i].volume for i in indices),
                series_key=bars[first].series_key,
                complete=True,
                n_minutes=HIGHER_MINUTES,
            )
        )
    return tuple(result)


@dataclass
class RenkoBuilder:
    last_close: float | None = None
    direction: int = 0
    brick_size: float | None = None
    bricks: int = 0

    def add(self, price: float, size: float | None) -> None:
        if size is None or size <= 0:
            return
        if self.last_close is None:
            self.last_close = price
            self.brick_size = size
            return
        while True:
            current = self.last_close
            if self.direction >= 0 and price >= current + size:
                self.last_close = current + size
                self.direction = 1
                self.brick_size = size
                self.bricks += 1
                continue
            if self.direction <= 0 and price <= current - size:
                self.last_close = current - size
                self.direction = -1
                self.brick_size = size
                self.bricks += 1
                continue
            if self.direction == 1 and price <= current - 2 * size:
                self.last_close = current - size
                self.direction = -1
                self.brick_size = size
                self.bricks += 1
                continue
            if self.direction == -1 and price >= current + 2 * size:
                self.last_close = current + size
                self.direction = 1
                self.brick_size = size
                self.bricks += 1
                continue
            return


def build_renko_series(
    bars: tuple[Any, ...], atr_timeframe: str, anchor: str, mode: str
) -> tuple[tuple[float | None, ...], tuple[float | None, ...], int]:
    """Return chart-bar Renko open/close values and completed-brick count.

    Closed mode exposes the last completed source bar; forming mode includes the
    current source bar prefix. ATR(3) is calculated on the selected source bars and
    frozen for every brick emitted while that source bar is processed.
    """
    if atr_timeframe not in ATR_TIMEFRAMES or anchor not in ANCHORS or mode not in MODES:
        raise ValueError("invalid Renko grid value")
    source = bars if atr_timeframe == "3m" else aggregate_54m(bars, anchor)
    builder = RenkoBuilder()
    opens: list[float | None] = [None] * len(bars)
    closes: list[float | None] = [None] * len(bars)
    previous_state: tuple[float | None, float | None] = (None, None)
    processed_source = -1
    groups = _groups(bars, anchor) if atr_timeframe == "54m" else []
    group_to_source: dict[datetime, int] = {bar.ts_utc: i for i, bar in enumerate(source)}

    def assign(i: int) -> None:
        opens[i], closes[i] = previous_state

    for i, bar in enumerate(bars):
        if atr_timeframe == "3m":
            cutoff = i if mode == "forming" else i - 1
            while processed_source < cutoff:
                processed_source += 1
                atr = port._atr(source[: processed_source + 1], period=3)[-1]
                builder.add(source[processed_source].close, atr)
                if builder.last_close is not None and builder.bricks:
                    size = builder.brick_size or 0.0
                    previous_state = (
                        builder.last_close - size
                        if builder.direction > 0
                        else builder.last_close + size,
                        builder.last_close,
                    )
            assign(i)
            continue
        key = bucket_start(bar.ts_utc, anchor)
        current_group = next((indices for start, indices in groups if start == key), [])
        current_position = current_group.index(i) if i in current_group else 0
        if mode == "closed":
            target_group = group_to_source.get(key, 0) - 1
            while processed_source < target_group:
                processed_source += 1
                source_bar = source[processed_source]
                atr = port._atr(source[: processed_source + 1], period=3)[-1]
                builder.add(source_bar.close, atr)
                if builder.last_close is not None and builder.bricks:
                    size = builder.brick_size or 0.0
                    previous_state = (
                        builder.last_close - size
                        if builder.direction > 0
                        else builder.last_close + size,
                        builder.last_close,
                    )
            assign(i)
            continue
        group_index = group_to_source.get(key)
        if group_index is None:
            assign(i)
            continue
        # Forming mode uses the current 54m prefix as a temporary source bar.
        prefix = current_group[: current_position + 1]
        partial = port.Candle(
            ts_utc=key,
            open=bars[prefix[0]].open,
            high=max(bars[j].high for j in prefix),
            low=min(bars[j].low for j in prefix),
            close=bars[prefix[-1]].close,
            volume=sum(bars[j].volume for j in prefix),
            series_key=bars[prefix[0]].series_key,
            complete=False,
            n_minutes=(current_position + 1) * port.TIMEFRAME,
        )
        history = list(source[:group_index]) + [partial]
        atr = port._atr(history, period=3)[-1]
        builder.add(partial.close, atr)
        if builder.last_close is not None and builder.bricks:
            size = builder.brick_size or 0.0
            previous_state = (
                builder.last_close - size if builder.direction > 0 else builder.last_close + size,
                builder.last_close,
            )
        assign(i)
    return tuple(opens), tuple(closes), builder.bricks


def replay_variant(
    bars: tuple[Any, ...], atr_timeframe: str, anchor: str, mode: str
) -> tuple[tuple[port.PortTrade, ...], port.Features, int]:
    original = port.compute_features

    def compute(values: Sequence[Any], selected: str) -> port.Features:
        base = original(values, selected)
        renko_open, renko_close, bricks = build_renko_series(
            tuple(values), atr_timeframe, anchor, selected
        )
        buy, sell = port._crosses(renko_open, renko_close)
        return replace(
            base, ha_open=renko_open, ha_close=renko_close, buy_cross=buy, sell_cross=sell
        )

    port.compute_features = compute
    try:
        trades, features = port.replay(bars, mode)
        _, _, bricks = build_renko_series(bars, atr_timeframe, anchor, mode)
        return trades, features, bricks
    finally:
        port.compute_features = original


def variant_result(
    owners: tuple[Any, ...], bars: tuple[Any, ...], atr_timeframe: str, anchor: str, mode: str
) -> dict[str, Any]:
    trades, features, bricks = replay_variant(bars, atr_timeframe, anchor, mode)
    timing = t172.cross_timing(owners, bars, features)
    return {
        "atr_timeframe": atr_timeframe,
        "anchor": anchor,
        "mode": mode,
        "port_trades": len(trades),
        "renko_bricks": bricks,
        "renko_crosses": sum(features.buy_cross) + sum(features.sell_cross),
        "matches": t172.matches(owners, trades),
        "cross_timing": timing,
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
        variant_result(owners, bars, atr_timeframe, anchor, mode)
        for atr_timeframe in ATR_TIMEFRAMES
        for anchor in ANCHORS
        for mode in MODES
    ]
    baseline = next(
        item
        for item in variants
        if item["atr_timeframe"] == "54m" and item["anchor"] == "epoch" and item["mode"] == "closed"
    )
    best = max(
        variants,
        key=lambda item: (
            item["cross_timing"]["within_2_bars_same_direction"],
            item["matches"]["within_2"],
        ),
    )
    target = any(item["matches"]["within_2"] >= 25 for item in variants)
    verdict = {
        "renko_explains": target,
        "best_variant": f"{best['atr_timeframe']}, {best['anchor']}, {best['mode']}",
        "best_within_2_cross": best["cross_timing"]["within_2_bars_same_direction"],
        "best_matches": best["matches"],
        "any_variant_at_least_25_within_2": target,
    }
    payload = {
        "task": "T-174",
        "warmup_start": warm_start.isoformat(),
        "end": end.isoformat(),
        "bars": len(bars),
        "owner_entries": len(owners),
        "baseline_open_close_t173": {
            "variant": "T-173 tfmult=18 epoch closed",
            "within_2_cross": 1,
            "matches": {"within_0": 1, "within_1": 3, "within_2": 4, "within_3": 4},
            "renko_54m_epoch_closed": {
                "within_2_cross": baseline["cross_timing"]["within_2_bars_same_direction"],
                "matches": baseline["matches"],
            },
        },
        "variants": variants,
        "verdict": verdict,
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
        " This gain is below 15/31 and is noise across 8 variants; it is not a finding."
        if verdict["best_within_2_cross"] < 15
        else ""
    )
    next_cause = (
        " The next likely cause is refresh-entry timing, a different data source "
        "(NQ1! futures versus NSXUSD CFD bars), or a different Pine version; "
        "no further variant was tested."
        if verdict["best_within_2_cross"] <= 8
        else ""
    )
    line = (
        "**Verdict:** Renko setup explains it "
        f"{'yes' if target else 'no'}; best variant {verdict['best_variant']}: owner enters "
        f"{verdict['best_within_2_cross']} of {len(owners)} "
        "within 2 bars of a same-direction cross; "
        f"matched exact/+/-1/+/-2/+/-3 = {best['matches']['within_0']}/"
        f"{best['matches']['within_1']}/"
        f"{best['matches']['within_2']}/{best['matches']['within_3']} of {len(owners)} "
        "(Open/Close baseline 1/3/4/4); any variant >=25 of 31 within +/-2: "
        f"{'yes' if target else 'no'}."
    )
    report = [
        "# T-174 Renko setup diagnostic",
        "",
        line + caution + next_cause,
        "",
        "The registered grid contains 8 replay-only variants on NSXUSD 3m bars "
        "with seven days of full warm-up. Renko uses close-based bricks, ATR(3) "
        "frozen at brick formation, and a two-brick reversal. Closed mode uses "
        "the last completed source bar; forming mode includes the current source-bar "
        "prefix. The 54m source is session-anchored by epoch or New York midnight; "
        "the 3m source uses each chart bar. No broker, orders, holdout, lake writes, "
        "production state, tuning or strategy-search activity occurred.",
        "",
        "## Per-variant results",
        "",
        "```text",
        json.dumps(variants, indent=2),
        "```",
    ]
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
