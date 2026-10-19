#!/usr/bin/env python3
"""Measure T-162 signal fragility under registered OHLC noise."""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import lab_t162_pine_port as port  # noqa: E402
from scripts.lab_t176_geometry import load, positions  # noqa: E402
from scripts.lab_t177_signals import signal_rows  # noqa: E402

NOISE_SDS = (0.5, 1.0, 2.0, 4.0)
DRAWS = 200
WINDOWS = (0, 3, 30)
BASIS_REFERENCE = 221.05


def perturb_bar(bar: Any, sigma: float, rng: random.Random) -> Any:
    values = [
        bar.open + rng.gauss(0.0, sigma),
        bar.high + rng.gauss(0.0, sigma),
        bar.low + rng.gauss(0.0, sigma),
        bar.close + rng.gauss(0.0, sigma),
    ]
    return replace(
        bar,
        open=values[0],
        high=max(values),
        low=min(values),
        close=values[3],
    )


def perturb_bars(bars: Iterable[Any], sigma: float, rng: random.Random) -> tuple[Any, ...]:
    return tuple(perturb_bar(bar, sigma, rng) for bar in bars)


def same_direction_count(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]], window: int
) -> int:
    return sum(
        any(
            row["direction"] == signal["direction"]
            and abs(row["index"] - signal["index"]) <= window
            for row in candidate
        )
        for signal in baseline
    )


def new_signal_count(baseline: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> int:
    return sum(
        not any(
            row["direction"] == signal["direction"]
            and abs(row["index"] - signal["index"]) <= 30
            for signal in baseline
        )
        for row in candidate
    )


def owner_match_count(
    owner_indices: list[int | None],
    directions: list[str],
    candidate: list[dict[str, Any]],
    window: int,
) -> int:
    return sum(
        index is not None
        and any(
            row["direction"] == direction and abs(row["index"] - index) <= window
            for row in candidate
        )
        for index, direction in zip(owner_indices, directions, strict=True)
    )


def percentile(values: list[int], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def summarize(values: list[int]) -> dict[str, float]:
    return {
        "mean": sum(values) / len(values),
        "p05": percentile(values, 0.05),
        "p95": percentile(values, 0.95),
    }


def run_draws(
    bars: tuple[Any, ...],
    baseline: list[dict[str, Any]],
    owner_indices: list[int | None],
    directions: list[str],
    sigma: float,
    rng: random.Random,
) -> dict[str, Any]:
    exact: list[int] = []
    close: list[int] = []
    broad: list[int] = []
    new: list[int] = []
    owner_close: list[int] = []
    owner_broad: list[int] = []
    for _ in range(DRAWS):
        candidate = signal_rows(perturb_bars(bars, sigma, rng), "closed")
        exact.append(same_direction_count(baseline, candidate, 0))
        close.append(same_direction_count(baseline, candidate, 3))
        broad.append(same_direction_count(baseline, candidate, 30))
        new.append(new_signal_count(baseline, candidate))
        owner_close.append(owner_match_count(owner_indices, directions, candidate, 3))
        owner_broad.append(owner_match_count(owner_indices, directions, candidate, 30))
    return {
        "draws": DRAWS,
        "baseline_signal_count": len(baseline),
        "baseline_reappears_exact": summarize(exact),
        "baseline_reappears_within_3": summarize(close),
        "baseline_reappears_within_30": summarize(broad),
        "new_signals_no_baseline_within_30": summarize(new),
        "owner_entries_within_3": summarize(owner_close),
        "owner_entries_within_30": summarize(owner_broad),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    rows, entries = load(args.owner_file)
    owner_positions = positions(rows, entries)
    with args.signals.open(encoding="utf-8") as handle:
        baseline_payload = json.load(handle)
    baseline = baseline_payload["signals"]
    start = min(row["dt"] for row in entries) - timedelta(days=7)
    end = max(row["dt"] for row in rows) + timedelta(minutes=3)
    minutes = port.read_nas100_minutes(args.lake, start, end)
    bars = tuple(port.resample_ohlc(minutes, port.TIMEFRAME))
    owner_indices = [
        port._bar_at(bars, datetime.fromisoformat(item["entry_time"]))[0]
        for item in owner_positions
    ]
    directions = [item["direction"] for item in owner_positions]
    close_diffs = [
        abs(bars[index].close - bars[index - 1].close) for index in range(1, len(bars))
    ]
    rng = random.Random(179)
    per_sd = {
        str(sigma): run_draws(bars, baseline, owner_indices, directions, sigma, rng)
        for sigma in NOISE_SDS
    }
    payload = {
        "task": "T-179",
        "bars": len(bars),
        "baseline_signals": len(baseline),
        "noise_sds_index_points": list(NOISE_SDS),
        "draws_per_sd": DRAWS,
        "seed": 179,
        "median_consecutive_close_difference_points": median(close_diffs),
        "basis_reference": {
            "source": "T-164 open-price NQ1/NAS100 median basis",
            "points": BASIS_REFERENCE,
            "comparison": {str(sigma): "smaller" for sigma in NOISE_SDS},
        },
        "per_sd": per_sd,
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "holdout_reads": 0,
            "lake_writes": 0,
            "production_state_writes": 0,
            "tuning": False,
            "secrets": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    selected = per_sd["1.0"]
    exact_pct = 100 * selected["baseline_reappears_exact"]["mean"] / len(baseline)
    close_pct = 100 * selected["baseline_reappears_within_3"]["mean"] / len(baseline)
    new_mean = selected["new_signals_no_baseline_within_30"]["mean"]
    owner3 = selected["owner_entries_within_3"]["mean"]
    owner30 = selected["owner_entries_within_30"]["mean"]
    fragile = exact_pct < 50.0
    report = [
        "# T-179 signal fragility",
        "",
        (
            f"Signals fragile **{'yes' if fragile else 'no'}**: at sd 1 point, only "
            f"{exact_pct:.1f}% of baseline signals reappear at the same bar and "
            f"{close_pct:.1f}% within +/-3 bars; new signals per draw: {new_mean:.1f}. "
            f"Owner entries within +/-3 bars: {owner3:.1f}/31 (baseline 3/31), "
            f"within +/-30: {owner30:.1f}/31 (baseline 15/31). The stored basis "
            f"difference ({BASIS_REFERENCE:.2f} points) is larger than every registered "
            "noise sd. Fragility supports treating bar-exact CFD matching as an unfair "
            "test and considering Q-071 option A; stability would support a different "
            "script, version or settings."
        ),
        "",
        f"Median absolute consecutive close difference: {median(close_diffs):.4f} points.",
        "Per-sd means and 5th..95th percentiles:",
        json.dumps({key: value for key, value in per_sd.items()}, sort_keys=True),
        "",
        "Replay only: no broker calls, orders, holdout reads, lake writes, tuning or secrets.",
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
