#!/usr/bin/env python3
"""Measure delay structure and unmatched owner entries from T-177 signals."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from statistics import quantiles
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import lab_t162_pine_port as port  # noqa: E402
from scripts.lab_t176_geometry import load, positions  # noqa: E402

NY = ZoneInfo("America/New_York")
BIN_EDGES = tuple(range(-60, 61, 6))
WINDOW = 30


def nearest_signal(
    owner_index: int | None,
    direction: str,
    signals: list[dict[str, Any]],
) -> int | None:
    """Return owner-minus-signal offset in bars for a same-direction signal."""
    if owner_index is None:
        return None
    candidates = [row["index"] for row in signals if row["direction"] == direction]
    if not candidates:
        return None
    signal_index = min(candidates, key=lambda value: abs(value - owner_index))
    return owner_index - signal_index


def nearest_owner(
    signal_index: int,
    direction: str,
    owner_indices: list[int | None],
    directions: list[str],
) -> int | None:
    candidates = [
        index
        for index, owner_direction in zip(owner_indices, directions, strict=True)
        if index is not None and owner_direction == direction
    ]
    if not candidates:
        return None
    owner_index = min(candidates, key=lambda value: abs(value - signal_index))
    return owner_index - signal_index


def histogram(offsets: Iterable[int | None]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for offset in offsets:
        if offset is None or offset < -60 or offset > 60:
            counts["outside"] += 1
            continue
        lower = max(-60, ((offset + 60) // 6) * 6 - 60)
        upper = lower + 5
        counts[f"{lower:+d}..{upper:+d}"] += 1
    labels = [f"{edge:+d}..{min(edge + 5, 60):+d}" for edge in BIN_EDGES]
    return {label: counts[label] for label in [*labels, "outside"]}


def quartiles(values: list[int]) -> dict[str, float | None]:
    if not values:
        return {"q1": None, "median": None, "q3": None, "iqr": None}
    if len(values) == 1:
        q1 = median = q3 = float(values[0])
    else:
        q1, median, q3 = quantiles(values, n=4, method="inclusive")
    return {"q1": q1, "median": median, "q3": q3, "iqr": q3 - q1}


def delay_stats(offsets: list[int | None]) -> dict[str, Any]:
    within = [value for value in offsets if value is not None and abs(value) <= WINDOW]
    stats = quartiles(within)
    stats.update(
        {
            "within_30": len(within),
            "total": len(offsets),
            "positive": sum(value > 0 for value in within),
            "positive_share": sum(value > 0 for value in within) / len(within) if within else None,
        }
    )
    return stats


def precision(
    signals: list[dict[str, Any]],
    owner_indices: list[int | None],
    directions: list[str],
) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    for signal in signals:
        offset = nearest_owner(signal["index"], signal["direction"], owner_indices, directions)
        matched.append(
            {
                **signal,
                "owner_minus_signal": offset,
                "matched": offset is not None and abs(offset) <= WINDOW,
            }
        )
    return {
        "matched": sum(item["matched"] for item in matched),
        "total": len(signals),
        "per_day": dict(
            Counter(
                datetime.fromisoformat(item["time"]).astimezone(NY).date().isoformat()
                for item in matched
                if item["matched"]
            )
        ),
        "rows": matched,
    }


def failed_conditions(
    bars: Any,
    features: Any,
    index: int | None,
    direction: str,
) -> list[str]:
    if index is None:
        return ["missing_bar"]
    cross = features.buy_cross[index] if direction == "long" else features.sell_cross[index]
    smart = port._smart_ok(bars, features, index, direction)
    risk_ok, _ = port._risk(bars, features, index, direction)
    return [
        label
        for label, passed in (
            ("cross", cross),
            ("smart_filter", smart),
            ("risk_cap", risk_ok),
        )
        if not passed
    ]


def pvalue(null_values: list[float], observed_value: float, *, lower: bool = False) -> float:
    if lower:
        extreme = sum(value <= observed_value for value in null_values)
    else:
        extreme = sum(value >= observed_value for value in null_values)
    return (1 + extreme) / (len(null_values) + 1)


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
        signal_payload = json.load(handle)
    signals = signal_payload["signals"]
    start = min(row["dt"] for row in entries) - timedelta(days=7)
    end = max(row["dt"] for row in rows) + timedelta(minutes=3)
    minutes = port.read_nas100_minutes(args.lake, start, end)
    bars = tuple(port.resample_ohlc(minutes, port.TIMEFRAME))
    features = port.compute_features(bars, "closed")
    owner_indices = [
        port._bar_at(bars, datetime.fromisoformat(item["entry_time"]))[0]
        for item in owner_positions
    ]
    directions = [item["direction"] for item in owner_positions]
    offsets = [
        nearest_signal(index, direction, signals)
        for index, direction in zip(owner_indices, directions, strict=True)
    ]
    observed = delay_stats(offsets)
    unmatched = []
    for item, index, direction, offset in zip(
        owner_positions, owner_indices, directions, offsets, strict=True
    ):
        if offset is not None and abs(offset) <= WINDOW:
            continue
        any_candidates = signals if index is not None else []
        any_signal = min(
            any_candidates,
            key=lambda row: abs(row["index"] - index),
            default=None,
        )
        unmatched.append(
            {
                "entry_time_ny": datetime.fromisoformat(item["entry_time"])
                .astimezone(NY)
                .isoformat(),
                "direction": direction,
                "same_offset_owner_minus_signal": offset,
                "nearest_any_direction": {
                    "direction": any_signal["direction"],
                    "time": any_signal["time"],
                    "owner_minus_signal": index - any_signal["index"],
                }
                if any_signal
                else None,
                "failed_conditions": failed_conditions(bars, features, index, direction),
            }
        )

    by_hour: dict[int, list[int]] = {}
    for index, bar in enumerate(bars):
        by_hour.setdefault(bar.ts_utc.astimezone(NY).hour, []).append(index)
    owner_hours = [
        datetime.fromisoformat(item["entry_time"]).astimezone(NY).hour for item in owner_positions
    ]
    rng = random.Random(178)
    null_iqrs: list[float] = []
    null_signs: list[float] = []
    null_precisions: list[int] = []
    for _ in range(2000):
        sampled = [rng.choice(by_hour.get(hour, list(range(len(bars))))) for hour in owner_hours]
        null_offsets = [
            nearest_signal(index, direction, signals)
            for index, direction in zip(sampled, directions, strict=True)
        ]
        null_delay = delay_stats(null_offsets)
        if null_delay["iqr"] is not None:
            null_iqrs.append(float(null_delay["iqr"]))
        if null_delay["positive_share"] is not None:
            null_signs.append(float(null_delay["positive_share"]))
        null_precisions.append(precision(signals, sampled, directions)["matched"])

    port_precision = precision(signals, owner_indices, directions)
    observed_iqr = float(observed["iqr"] or 0.0)
    observed_sign = float(observed["positive_share"] or 0.0)
    null = {
        "draws": 2000,
        "seed": 178,
        "delay_iqr_mean": sum(null_iqrs) / len(null_iqrs),
        "delay_iqr_p_narrow": pvalue(null_iqrs, observed_iqr, lower=True),
        "positive_share_mean": sum(null_signs) / len(null_signs),
        "positive_share_p_high": pvalue(null_signs, observed_sign),
        "port_precision_mean": sum(null_precisions) / len(null_precisions),
        "port_precision_p_high": pvalue(null_precisions, port_precision["matched"]),
    }
    payload = {
        "task": "T-178",
        "signals_source": "docs/evidence/t177_signals.json",
        "bars": len(bars),
        "owner_entries": len(owner_positions),
        "same_direction_offsets_owner_minus_signal": offsets,
        "offset_histogram": histogram(offsets),
        "observed_delay": observed,
        "unmatched_entries": unmatched,
        "port_precision": port_precision,
        "permutation": null,
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
    positive = int(observed["positive"])
    total = int(observed["within_30"])
    majority = Counter(condition for item in unmatched for condition in item["failed_conditions"])
    common = majority.most_common(1)[0][0] if majority else "none"
    delay_verdict = (
        "yes"
        if null["delay_iqr_p_narrow"] < 0.05 and null["positive_share_p_high"] < 0.05
        else "no"
    )
    report = [
        "# T-178 delay structure and unmatched entries",
        "",
        (
            f"Delay structure **{delay_verdict}**: of the {total} entries within 30 bars, "
            f"{positive} were after the signal (median {observed['median']:+.1f} bars, "
            f"IQR {observed['q1']:.1f}..{observed['q3']:.1f}); null p for consistent sign "
            f"= {null['positive_share_p_high']:.4f}, null p for a narrow IQR = "
            f"{null['delay_iqr_p_narrow']:.4f}; port-side precision {port_precision['matched']}/"
            f"{port_precision['total']}, null mean {null['port_precision_mean']:.2f}. "
            f"The {len(unmatched)} unmatched entries fail mostly on {common}. "
            f"A positive stable delay supports owner entries placed manually after signals; "
            "no stable delay supports a different script, version or settings."
        ),
        "",
        (
            "Same-direction signed-offset histogram (owner minus signal, bars): "
            f"{payload['offset_histogram']}"
        ),
        f"Port-side matched signals by New York day: {port_precision['per_day']}.",
        (
            "Unmatched entries are tabulated in the JSON with nearest any-direction "
            "signal and failed conditions."
        ),
        "",
        "Replay only: no broker calls, orders, holdout reads, lake writes, tuning or secrets.",
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
