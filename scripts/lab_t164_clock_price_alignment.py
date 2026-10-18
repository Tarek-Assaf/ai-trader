#!/usr/bin/env python3
"""Diagnostic-only T-164 clock and price alignment scan.

The script compares the 31 owner entries with recorded NSXUSD mid bars over a
bounded constant time-shift grid.  It removes one robust constant price basis,
reports a permutation p-value, then evaluates the existing T-162 filters at the
best shift.  It never calls a broker, reads holdout data, writes the lake or
changes production state.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

try:
    from trader.data.resample import Candle, resample_ohlc
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from trader.data.resample import Candle, resample_ohlc

try:
    from scripts import lab_t162_pine_port as port
    from scripts import lab_t163_filter_anchor_diagnosis as t163
    from scripts.lab_t151_owner_trades import OwnerEntry, read_nas100_minutes, read_owner_entries
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import lab_t162_pine_port as port
    from scripts import lab_t163_filter_anchor_diagnosis as t163
    from scripts.lab_t151_owner_trades import OwnerEntry, read_nas100_minutes, read_owner_entries

OWNER_ZONE = ZoneInfo("America/New_York")
SHIFT_MINUTES = tuple(range(-360, 361))
BASIS_NAMES = ("open", "close", "hlc3")
PERMUTATIONS = 2_000
PERMUTATION_SEED = 164


@dataclass(frozen=True, slots=True)
class Alignment:
    shift_minutes: int
    basis: str
    basis_offset: float
    median_abs_residual: float
    samples: int


def _basis(bar: Candle, name: str) -> float:
    if name == "open":
        return bar.open
    if name == "close":
        return bar.close
    if name == "hlc3":
        return (bar.high + bar.low + bar.close) / 3.0
    raise ValueError(f"unknown price basis: {name}")


def _bar_for_shift(
    by_minute: dict[datetime, Candle], stamp: datetime, shift: int
) -> Candle | None:
    shifted = (stamp + timedelta(minutes=shift)).replace(second=0, microsecond=0)
    return by_minute.get(shifted)


def _alignment_for_values(prices: Sequence[float], values: Sequence[float]) -> tuple[float, float]:
    differences = [price - value for price, value in zip(prices, values, strict=True)]
    offset = float(median(differences))
    residual = float(median(abs(item - offset) for item in differences))
    return offset, residual


def scan_alignments(
    owners: Sequence[OwnerEntry], minutes: Sequence[Candle]
) -> tuple[list[Alignment], dict[str, list[dict[str, object]]]]:
    by_minute = {bar.ts_utc.replace(second=0, microsecond=0): bar for bar in minutes}
    prices = [entry.entry_price for entry in owners]
    results: list[Alignment] = []
    per_entry: dict[str, list[dict[str, object]]] = {name: [] for name in BASIS_NAMES}
    for name in BASIS_NAMES:
        for shift in SHIFT_MINUTES:
            values: list[float] = []
            for owner in owners:
                bar = _bar_for_shift(by_minute, owner.entry_time, shift)
                if bar is not None:
                    values.append(_basis(bar, name))
            if len(values) < 2:
                continue
            # Keep only owners with a bar for the same robust basis estimate.
            paired = [
                (owner.entry_price, _basis(bar, name))
                for owner in owners
                if (bar := _bar_for_shift(by_minute, owner.entry_time, shift)) is not None
            ]
            paired_prices, paired_values = zip(*paired, strict=True)
            offset, residual = _alignment_for_values(paired_prices, paired_values)
            results.append(Alignment(shift, name, offset, residual, len(paired)))
    return results, per_entry


def _per_entry_best_shifts(
    owners: Sequence[OwnerEntry], minutes: Sequence[Candle], alignment: Alignment
) -> list[dict[str, object]]:
    by_minute = {bar.ts_utc.replace(second=0, microsecond=0): bar for bar in minutes}
    rows: list[dict[str, object]] = []
    for owner in owners:
        candidates: list[tuple[float, int, float, float]] = []
        for shift in SHIFT_MINUTES:
            bar = _bar_for_shift(by_minute, owner.entry_time, shift)
            if bar is None:
                continue
            value = _basis(bar, alignment.basis)
            residual = abs(owner.entry_price - value - alignment.basis_offset)
            candidates.append((residual, shift, value, owner.entry_price - value))
        if candidates:
            residual, shift, value, raw = min(candidates)
            rows.append({
                "direction": owner.direction,
                "owner_time_ny": owner.entry_time.astimezone(OWNER_ZONE).isoformat(),
                "owner_time_utc": owner.entry_time.isoformat(),
                "owner_price": owner.entry_price,
                "best_shift_minutes": shift,
                "residual_after_basis": residual,
                "raw_basis_difference": raw,
                "recorded_value": value,
            })
    return rows


def _permutation_p_value(
    owners: Sequence[OwnerEntry], minutes: Sequence[Candle], observed: float
) -> float:
    by_minute = {bar.ts_utc.replace(second=0, microsecond=0): bar for bar in minutes}
    observed_prices = [entry.entry_price for entry in owners]
    matrices: dict[str, list[list[float]]] = {name: [] for name in BASIS_NAMES}
    for name in BASIS_NAMES:
        for shift in SHIFT_MINUTES:
            values = [
                _basis(bar, name)
                for owner in owners
                if (bar := _bar_for_shift(by_minute, owner.entry_time, shift)) is not None
            ]
            if len(values) == len(owners):
                matrices[name].append(values)
    rng = random.Random(PERMUTATION_SEED)
    at_or_better = 0
    for _ in range(PERMUTATIONS):
        shuffled = observed_prices[:]
        rng.shuffle(shuffled)
        null_best = float("inf")
        for name in BASIS_NAMES:
            for values in matrices[name]:
                _, residual = _alignment_for_values(shuffled, values)
                null_best = min(null_best, residual)
        if null_best <= observed:
            at_or_better += 1
    return (1.0 + at_or_better) / (PERMUTATIONS + 1.0)


def _shifted_owner(owner: OwnerEntry, shift: int) -> OwnerEntry:
    return OwnerEntry(owner.entry_time + timedelta(minutes=shift), owner.direction, owner.entry_price, owner.quantity, owner.stop_price)


def _filter_counts(
    owners: Sequence[OwnerEntry], bars: Sequence[Candle], shift: int, label_shift: int = 0
) -> dict[str, object]:
    features = t163._features(bars, "closed", "ny_18:00", "ny_18:00_equal")
    shifted = [_shifted_owner(owner, shift + label_shift) for owner in owners]
    rows = []
    for owner in shifted:
        idx, bar = port._bar_at(bars, owner.entry_time)
        if idx is None or bar is None:
            continue
        rows.append(
            {
                "index": idx,
                "ema": bool(features.ema[idx] is not None and (bar.close > features.ema[idx] if owner.direction == "long" else bar.close < features.ema[idx])),
                "adx_di": bool(features.adx[idx] is not None and features.adx[idx] >= 20 and (features.plus_di[idx] > features.minus_di[idx] if owner.direction == "long" else features.minus_di[idx] > features.plus_di[idx])),
                "vwap_side": bool(features.vwap[idx] is not None and (bar.close > features.vwap[idx] if owner.direction == "long" else bar.close < features.vwap[idx])),
                "vwap_distance": bool(features.vwap[idx] is not None and features.atr[idx] is not None and abs(bar.close - features.vwap[idx]) / features.atr[idx] <= port.VWAP_DISTANCE_ATR),
            }
        )
    keys = ("ema", "adx_di", "vwap_side", "vwap_distance")
    counts = {key: sum(bool(row[key]) for row in rows) for key in keys}
    counts["all_four"] = sum(all(bool(row[key]) for key in keys) for row in rows)
    counts["rows_with_bar"] = len(rows)
    return counts


def _match_after_shift(owners: Sequence[OwnerEntry], bars: Sequence[Candle], shift: int) -> int:
    trades, _ = port.replay(bars, "closed")
    shifted = [_shifted_owner(owner, shift) for owner in owners]
    matched, _ = port._match(shifted, trades, 2 * port.TIMEFRAME)
    return matched


def _coverage(minutes: Sequence[Candle]) -> dict[str, object]:
    return {
        "NSXUSD": {
            "1m": len(minutes),
            "3m": len(tuple(item for item in resample_ohlc(minutes, 3) if item.complete)),
            "5m": len(tuple(item for item in resample_ohlc(minutes, 5) if item.complete)),
            "60m": len(tuple(item for item in resample_ohlc(minutes, 60) if item.complete)),
        },
        "XAUUSD": {"owner_rows": 0, "note": "no owner rows in this window"},
    }


def run(lake: Path, owner_file: Path) -> dict[str, object]:
    owners = read_owner_entries(owner_file)
    if len(owners) != 31:
        raise RuntimeError(f"expected 31 owner entries, found {len(owners)}")
    start = min(item.entry_time for item in owners) - timedelta(days=3)
    end = max(item.entry_time for item in owners) + timedelta(days=1)
    minutes = read_nas100_minutes(lake, start, end)
    if not minutes:
        raise RuntimeError("NSXUSD 1m data is missing")
    alignments, _ = scan_alignments(owners, minutes)
    best = min(alignments, key=lambda item: item.median_abs_residual)
    per_entry = {best.basis: _per_entry_best_shifts(owners, minutes, best)}
    p_value = _permutation_p_value(owners, minutes, best.median_abs_residual)
    bars = tuple(item for item in resample_ohlc(minutes, 3) if item.complete)
    filter_shift = best.shift_minutes if p_value < 0.01 else 0
    filters = _filter_counts(owners, bars, filter_shift)
    label_open = _filter_counts(owners, bars, filter_shift, 0)
    label_close = _filter_counts(owners, bars, filter_shift, -3)
    return {
        "task": "T-164",
        "generated_at": datetime.now(UTC).isoformat(),
        "owner_entries": len(owners),
        "owner_timezone": "America/New_York",
        "shift_grid": {"minimum": -360, "maximum": 360, "step": 1},
        "best_constant_alignment": {
            "shift_minutes": best.shift_minutes,
            "basis": best.basis,
            "basis_offset": best.basis_offset,
            "median_abs_residual": best.median_abs_residual,
            "samples": best.samples,
            "permutation_p_value": p_value,
            "clock_correct_at_zero": best.shift_minutes == 0,
        },
        "alignment_results": [asdict(item) for item in alignments],
        "per_entry_best_shifts": per_entry,
        "filter_shift_used": filter_shift,
        "filter_counts_at_best_shift": filters,
        "port_match_within_plus_minus_2_bars": _match_after_shift(owners, bars, filter_shift),
        "bar_label_convention": {"owner_time_is_bar_open": label_open, "owner_time_is_bar_close": label_close},
        "coverage": _coverage(minutes),
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "sealed_holdout_read": False,
            "lake_writes": 0,
            "production_state_writes": 0,
            "gate_evaluated": False,
            "tuning": False,
        },
    }


def report(data: dict[str, object]) -> str:
    best = data["best_constant_alignment"]
    match = data["port_match_within_plus_minus_2_bars"]
    if best["permutation_p_value"] < 0.01:
        verdict = f"Best alignment is {best['shift_minutes']} minutes using {best['basis']} (permutation p={best['permutation_p_value']:.4f}); the clock is {'correct' if best['shift_minutes'] == 0 else 'not aligned at zero'}. T-162 matches {match}/31 within +/-2 three-minute bars after the shift."
    else:
        verdict = f"No constant clock/price alignment is significant (best shift {best['shift_minutes']} minutes, permutation p={best['permutation_p_value']:.4f}); T-162 matches {match}/31 within +/-2 three-minute bars. Ask the owner for a chart-bar CSV export or Pine Inputs/Style screenshot."
    lines = ["# T-164 clock/price alignment", "", "**Verdict:** " + verdict, "", "Diagnostic-only offline replay; no broker calls, orders, holdout reads, lake writes, production writes or tuning.", "", "## Best alignment", "", json.dumps(best, indent=2), "", "## Filter counts at selected shift", "", json.dumps(data["filter_counts_at_best_shift"], indent=2), "", "## Bar-label convention", "", json.dumps(data["bar_label_convention"], indent=2), "", "## Coverage", "", json.dumps(data["coverage"], indent=2), "", "## Safety", "", json.dumps(data["safety"], indent=2)]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--entries", type=Path, required=True)
    args = parser.parse_args()
    data = run(args.lake, args.owner_file)
    args.output.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    rows = []
    for basis, entries in data["per_entry_best_shifts"].items():
        for row in entries:
            rows.append({"basis": basis, **row})
    fields = sorted({key for row in rows for key in row})
    with args.entries.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    args.report.write_text(report(data), encoding="utf-8")
    print(json.dumps({"task": "T-164", "best": data["best_constant_alignment"], "safety": data["safety"]}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
