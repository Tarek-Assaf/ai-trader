#!/usr/bin/env python3
"""T-159 diagnostic and bounded time-alignment correction study.

This script reads only the owner replay CSV and the unsealed training lake. It
never calls a broker, reads the sealed holdout, rewrites the lake, tunes rules,
or writes production state. It confirms a clock offset before any correction.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts.lab_t151_owner_trades import read_nas100_minutes
except ModuleNotFoundError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.lab_t151_owner_trades import read_nas100_minutes


@dataclass(frozen=True, slots=True)
class Trade:
    number: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float


def read_trades(path: Path) -> tuple[Trade, ...]:
    rows: dict[str, dict[str, tuple[datetime, float]]] = defaultdict(dict)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            number = str(row.get("Trade number", "")).strip()
            kind = str(row.get("Type", "")).lower()
            if not number or not (kind.startswith("entry") or kind.startswith("exit")):
                continue
            try:
                stamp = datetime.strptime(str(row["Date and time"]), "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
                price = float(row["Price USD"])
            except (KeyError, TypeError, ValueError):
                continue
            rows[number]["entry" if kind.startswith("entry") else "exit"] = (stamp, price)
    return tuple(
        Trade(number, values["entry"][0], values["exit"][0], values["entry"][1], values["exit"][1])
        for number, values in sorted(rows.items(), key=lambda item: item[1].get("entry", (datetime.max.replace(tzinfo=UTC), 0.0))[0])
        if "entry" in values and "exit" in values
    )


def _correlation(moves: Sequence[tuple[float, float]]) -> float | None:
    if len(moves) < 2:
        return None
    left = [item[0] for item in moves]
    right = [item[1] for item in moves]
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_dev = math.sqrt(math.fsum((value - left_mean) ** 2 for value in left))
    right_dev = math.sqrt(math.fsum((value - right_mean) ** 2 for value in right))
    if not left_dev or not right_dev:
        return None
    return math.fsum((a - left_mean) * (b - right_mean) for a, b in moves) / (left_dev * right_dev)


def score_shift(trades: Sequence[Trade], prices: dict[datetime, float], offset_minutes: int) -> dict[str, Any]:
    delta = timedelta(minutes=offset_minutes)
    moves: list[tuple[float, float]] = []
    agreement = 0
    for trade in trades:
        left = prices.get(trade.entry_time + delta)
        right = prices.get(trade.exit_time + delta)
        if left is None or right is None:
            continue
        owner_move = trade.exit_price - trade.entry_price
        recorded_move = right - left
        moves.append((owner_move, recorded_move))
        agreement += int((owner_move > 0) == (recorded_move > 0))
    return {
        "offset_minutes": offset_minutes,
        "comparable": len(moves),
        "agreement": agreement / len(moves) if moves else None,
        "correlation": _correlation(moves),
    }


def _permutation_p_value(
    trades: Sequence[Trade], prices: dict[datetime, float], offsets: Sequence[int], observed: float
) -> dict[str, Any]:
    rng = random.Random(159)
    moves = [trade.exit_price - trade.entry_price for trade in trades]
    maxima: list[float] = []
    for _ in range(2000):
        shuffled = list(moves)
        rng.shuffle(shuffled)
        best = 0.0
        for offset in offsets:
            delta = timedelta(minutes=offset)
            comparable = agree = 0
            for trade, owner_move in zip(trades, shuffled, strict=True):
                left = prices.get(trade.entry_time + delta)
                right = prices.get(trade.exit_time + delta)
                if left is None or right is None:
                    continue
                comparable += 1
                agree += int((owner_move > 0) == ((right - left) > 0))
            if comparable:
                best = max(best, agree / comparable)
        maxima.append(best)
    ordered = sorted(maxima)
    return {
        "permutations": len(maxima),
        "observed_best_agreement": observed,
        "null_max_median": statistics.median(maxima),
        "null_max_p95": ordered[int(0.95 * len(ordered)) - 1],
        "p_value": (1 + sum(value >= observed for value in maxima)) / (len(maxima) + 1),
    }


def _gap_check(minutes: Sequence[Any]) -> dict[str, Any]:
    stamps = [item.ts_utc for item in minutes]
    gaps = [int((right - left).total_seconds() // 60) for left, right in zip(stamps[:-1], stamps[1:], strict=True)]
    large = [
        {"from": left.isoformat(), "to": right.isoformat(), "gap_minutes": gap}
        for left, right, gap in zip(stamps[:-1], stamps[1:], gaps, strict=True)
        if gap > 10
    ]
    return {"max_gap_minutes": max(gaps) if gaps else None, "gap_histogram": dict(Counter(gaps).most_common(12)), "large_examples": large[:20]}


def run(lake: Path, owner_file: Path) -> dict[str, Any]:
    trades = read_trades(owner_file)
    start = min(item.entry_time for item in trades) - timedelta(days=2)
    end = max(item.exit_time for item in trades) + timedelta(days=2)
    minutes = read_nas100_minutes(lake, start, end)
    prices = {item.ts_utc: item.close for item in minutes}
    offsets = tuple(range(-360, 361, 5))
    scores = tuple(score_shift(trades, prices, offset) for offset in offsets)
    valid = tuple(item for item in scores if item["agreement"] is not None)
    best = max(valid, key=lambda item: (item["agreement"], item["correlation"] or -9.0, item["comparable"]))
    zero = next(item for item in scores if item["offset_minutes"] == 0)
    mod_counts = Counter(item.ts_utc.minute % 3 for item in minutes)
    owner_mod_counts = Counter(item.entry_time.minute % 3 for item in trades)
    return {
        "task": "T-159",
        "trades": len(trades),
        "lake_minutes": len(minutes),
        "stage1_offset_confirmation": {
            "grid_minutes": [-360, 360, 5],
            "zero_shift": zero,
            "best_shift": best,
            "top10": sorted(valid, key=lambda item: (item["agreement"], item["correlation"] or -9.0), reverse=True)[:10],
            "permutation": _permutation_p_value(trades, prices, offsets, float(best["agreement"])),
        },
        "stage2_session_gap_check": {
            "recorded_window": _gap_check(minutes),
            "known_schedule": {
                "cme_globex_daily_break_utc_before_2026_11_01": "21:00-22:00 UTC",
                "cme_globex_sunday_open_utc_before_2026_11_01": "Sunday 22:00 UTC",
                "dst_note": "US DST ends 2026-11-01; this owner window is 2026-10-06 through 2026-10-16",
            },
        },
        "stage3_timestamp_convention": {
            "lake_minute_mod_3": dict(mod_counts),
            "owner_entry_minute_mod_3": dict(owner_mod_counts),
            "bar_time": "resample_ohlc uses UTC bucket start; no close-time shift",
            "owner_csv_contract": "docs/specs/t151_owner_trades_comparison.md records owner confirmation that export times are UTC",
            "recorder_contract": "src/trader/data/recorder.py normalizes aware timestamps with astimezone(UTC) and truncates to minute",
            "resample_contract": "src/trader/data/resample.py floors epoch minutes to UTC timeframe buckets",
            "timezone_arithmetic": {
                "raw_owner_naive_as_utc": "no correction",
                "America/New_York_on_2026_10_06": "UTC-4, which would add +240 minutes, but contradicts the owner's UTC confirmation",
                "Asia/Riyadh": "UTC+3, not the observed +240-minute shift",
            },
        },
        "safety": {"broker_calls": 0, "orders": 0, "sealed_holdout_read": False, "lake_writes": 0, "production_state_writes": 0},
    }


def report(payload: dict[str, Any]) -> str:
    s1 = payload["stage1_offset_confirmation"]
    s2 = payload["stage2_session_gap_check"]
    s3 = payload["stage3_timestamp_convention"]
    best = s1["best_shift"]
    perm = s1["permutation"]
    verdict = (
        "STOP after confirmation: the +240-minute shift is not accepted as a fix because the "
        f"permutation max-shift test is not significant (p={perm['p_value']:.4f}) or the owner export contract says UTC."
        if perm["p_value"] >= 0.05
        else "The +240-minute shift is statistically repeatable; correction requires a separate reviewed change after locating the layer."
    )
    lines = ["# T-159 Time alignment", "", "## Owner verdict", "", verdict, "No lake rewrite or strategy-rule change was made.", "", "## Stage 1 — offset confirmation", "", json.dumps({"zero_shift": s1["zero_shift"], "best_shift": best, "permutation": perm}, indent=2, sort_keys=True), "", "## Stage 2 — session gaps", "", json.dumps(s2, indent=2, sort_keys=True), "", "## Stage 3 — timestamp convention and location", "", json.dumps(s3, indent=2, sort_keys=True), "", "## Safety", "", json.dumps(payload["safety"], indent=2, sort_keys=True)]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    payload = run(args.lake, args.owner_file)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.write_text(report(payload), encoding="utf-8")
    print(json.dumps({"task": "T-159", "trades": payload["trades"], "best_shift": payload["stage1_offset_confirmation"]["best_shift"], "p_value": payload["stage1_offset_confirmation"]["permutation"]["p_value"], "safety": payload["safety"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
