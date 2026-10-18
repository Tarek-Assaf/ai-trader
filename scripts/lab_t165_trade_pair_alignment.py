#!/usr/bin/env python3
"""Diagnostic-only T-165 within-trade price-move alignment scan.

The owner export contains paired entry and exit rows.  This script compares
signed entry-to-exit moves with the recorded NSXUSD 1m mid bars over a bounded
clock-shift grid, so a drifting NQ1/NAS100 price basis cancels inside a trade.
It is offline and never calls a broker, reads holdout data, or writes the lake.
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
    from scripts.lab_t151_owner_trades import parse_owner_datetime, read_nas100_minutes
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.lab_t151_owner_trades import parse_owner_datetime, read_nas100_minutes

OWNER_ZONE = ZoneInfo("America/New_York")
SHIFT_MINUTES = tuple(range(-360, 361))
PERMUTATIONS = 2_000
PERMUTATION_SEED = 164
POINT_VALUE_USD = 20.0  # inferred from the export's PnL and price moves


@dataclass(frozen=True, slots=True)
class OwnerTrade:
    number: str
    direction: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    favorable_points: float | None
    adverse_points: float | None
    duration_bars: int | None
    favorable_usd: float | None
    adverse_usd: float | None

    @property
    def signed_move(self) -> float:
        sign = 1.0 if self.direction == "long" else -1.0
        return (self.exit_price - self.entry_price) * sign


@dataclass(frozen=True, slots=True)
class MoveResult:
    shift_minutes: int
    samples: int
    sign_matches: int
    tolerance_matches: int
    median_abs_error: float | None


def _float(row: dict[str, str], key: str) -> float | None:
    value = row.get(key, "")
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def read_owner_trades(path: Path) -> tuple[OwnerTrade, ...]:
    groups: dict[str, list[dict[str, str]]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            number = str(row.get("Trade number", "")).strip()
            if number:
                groups.setdefault(number, []).append(row)
    # The export has 54 tranches but 31 distinct entry timestamps.  T-165's
    # acceptance count is 31, so collapse pyramids into one parent trade:
    # quantity-weight entry/exit prices, use the final exit timestamp, sum
    # excursion dollars and use the longest reported duration.
    parents: dict[tuple[str, str], list[tuple[dict[str, str], dict[str, str]]]] = {}
    for _number, rows in groups.items():
        entry = next((r for r in rows if str(r.get("Type", "")).lower().startswith("entry")), None)
        exit_row = next(
            (r for r in rows if str(r.get("Type", "")).lower().startswith("exit")), None
        )
        if entry is None or exit_row is None:
            continue
        kind = str(entry.get("Type", "")).lower()
        direction = "long" if "long" in kind else "short" if "short" in kind else ""
        if direction:
            parents.setdefault((str(entry["Date and time"]), direction), []).append(
                (entry, exit_row)
            )
    trades: list[OwnerTrade] = []
    for parent_index, ((entry_stamp, direction), pairs) in enumerate(
        sorted(parents.items()), start=1
    ):
        entry_time = parse_owner_datetime(entry_stamp)
        quantities = [abs(_float(entry, "Size (qty)") or 0.0) for entry, _ in pairs]
        quantity = sum(quantities)
        entry_price = (
            sum(
                float(entry["Price USD"]) * qty
                for (entry, _), qty in zip(pairs, quantities, strict=True)
            )
            / quantity
        )
        exit_time = max(
            parse_owner_datetime(str(exit_row["Date and time"])) for _, exit_row in pairs
        )
        exit_price = (
            sum(
                float(exit_row["Price USD"]) * qty
                for (_, exit_row), qty in zip(pairs, quantities, strict=True)
            )
            / quantity
        )
        fav_usd_values = [_float(entry, "Favorable excursion USD") for entry, _ in pairs]
        adv_usd_values = [_float(entry, "Adverse excursion USD") for entry, _ in pairs]
        fav_usd = (
            sum(value or 0.0 for value in fav_usd_values)
            if any(value is not None for value in fav_usd_values)
            else None
        )
        adv_usd = (
            sum(value or 0.0 for value in adv_usd_values)
            if any(value is not None for value in adv_usd_values)
            else None
        )
        fav_points = (
            fav_usd / (POINT_VALUE_USD * quantity) if fav_usd is not None and quantity else None
        )
        adv_points = (
            abs(adv_usd) / (POINT_VALUE_USD * quantity)
            if adv_usd is not None and quantity
            else None
        )
        durations = [
            int(value)
            for entry, _ in pairs
            if (value := _float(entry, "Duration (bars)")) is not None
        ]
        trades.append(
            OwnerTrade(
                str(parent_index),
                direction,
                entry_time,
                exit_time,
                entry_price,
                exit_price,
                quantity,
                fav_points,
                adv_points,
                max(durations) if durations else None,
                fav_usd,
                adv_usd,
            )
        )
    return tuple(trades)


def _bar_map(bars: Sequence[Candle]) -> dict[datetime, Candle]:
    return {bar.ts_utc.replace(second=0, microsecond=0): bar for bar in bars}


def _shifted_stamp(stamp: datetime, shift: int, *, close_label: bool = False) -> datetime:
    adjusted = stamp + timedelta(minutes=shift)
    if close_label:
        adjusted -= timedelta(minutes=3)
    return adjusted.replace(second=0, microsecond=0)


def _signed_move(
    trade: OwnerTrade, bars: dict[datetime, Candle], shift: int, *, close_label: bool = False
) -> float | None:
    entry = bars.get(_shifted_stamp(trade.entry_time, shift, close_label=close_label))
    exit_bar = bars.get(_shifted_stamp(trade.exit_time, shift, close_label=close_label))
    if entry is None or exit_bar is None:
        return None
    sign = 1.0 if trade.direction == "long" else -1.0
    return (exit_bar.close - entry.open) * sign


def _within_tolerance(value: float, target: float) -> bool:
    same_sign = (
        (value > 0 and target > 0) or (value < 0 and target < 0) or (value == 0 and target == 0)
    )
    return same_sign and abs(value - target) <= max(abs(target) * 0.25, 10.0)


def scan_moves(
    trades: Sequence[OwnerTrade],
    bars: Sequence[Candle],
    shifts: Sequence[int],
    *,
    close_label: bool = False,
) -> tuple[list[MoveResult], dict[int, list[float | None]]]:
    mapped = _bar_map(bars)
    results: list[MoveResult] = []
    values_by_shift: dict[int, list[float | None]] = {}
    for shift in shifts:
        values = [_signed_move(trade, mapped, shift, close_label=close_label) for trade in trades]
        values_by_shift[shift] = values
        paired = [
            (value, trade.signed_move)
            for value, trade in zip(values, trades, strict=True)
            if value is not None
        ]
        errors = [abs(value - target) for value, target in paired]
        results.append(
            MoveResult(
                shift_minutes=shift,
                samples=len(paired),
                sign_matches=sum((value > 0) == (target > 0) for value, target in paired),
                tolerance_matches=sum(_within_tolerance(value, target) for value, target in paired),
                median_abs_error=float(median(errors)) if errors else None,
            )
        )
    return results, values_by_shift


def _best(results: Sequence[MoveResult]) -> MoveResult:
    available = [item for item in results if item.samples]
    return min(
        available,
        key=lambda item: (
            -item.tolerance_matches,
            -item.sign_matches,
            item.median_abs_error or float("inf"),
            abs(item.shift_minutes),
        ),
    )


def permutation_p_value(
    trades: Sequence[OwnerTrade], values_by_shift: dict[int, list[float | None]], observed: int
) -> float:
    rng = random.Random(PERMUTATION_SEED)
    targets = [trade.signed_move for trade in trades]
    at_or_better = 0
    shifts = tuple(values_by_shift)
    for _ in range(PERMUTATIONS):
        shuffled = targets[:]
        rng.shuffle(shuffled)
        null_best = 0
        for shift in shifts:
            values = values_by_shift[shift]
            count = sum(
                _within_tolerance(value, target)
                for value, target in zip(values, shuffled, strict=True)
                if value is not None
            )
            null_best = max(null_best, count)
        if null_best >= observed:
            at_or_better += 1
    return (1.0 + at_or_better) / (PERMUTATIONS + 1.0)


def _path_excursion(
    trade: OwnerTrade, bars: Sequence[Candle], shift: int, *, close_label: bool = False
) -> tuple[float, float] | None:
    mapped = _bar_map(bars)
    start = _shifted_stamp(trade.entry_time, shift, close_label=close_label)
    end = _shifted_stamp(trade.exit_time, shift, close_label=close_label)
    if end < start:
        start, end = end, start
    path = [bar for stamp, bar in mapped.items() if start <= stamp <= end]
    entry = mapped.get(_shifted_stamp(trade.entry_time, shift, close_label=close_label))
    if not path or entry is None:
        return None
    sign = 1.0 if trade.direction == "long" else -1.0
    favorable = max(
        (sign * (bar.high - entry.open) if sign > 0 else sign * (bar.low - entry.open))
        for bar in path
    )
    adverse = max(
        ((-sign) * (bar.low - entry.open) if sign > 0 else (-sign) * (bar.high - entry.open))
        for bar in path
    )
    return max(0.0, favorable), max(0.0, adverse)


def excursion_summary(
    trades: Sequence[OwnerTrade], bars: Sequence[Candle], shift: int
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for trade in trades:
        observed = _path_excursion(trade, bars, shift)
        if observed is None:
            continue
        favorable, adverse = observed
        fav_ok = trade.favorable_points is None or favorable + 1e-9 >= trade.favorable_points * 0.75
        adv_ok = trade.adverse_points is None or adverse <= trade.adverse_points * 1.25
        rows.append(
            {
                "trade": trade.number,
                "owner_favorable_points": trade.favorable_points,
                "our_favorable_points": favorable,
                "favorable_reached_with_25pct_tolerance": fav_ok,
                "owner_adverse_points": trade.adverse_points,
                "our_adverse_points": adverse,
                "adverse_within_25pct_tolerance": adv_ok,
            }
        )
    return {
        "shift_minutes": shift,
        "samples": len(rows),
        "favorable_reached": sum(
            bool(row["favorable_reached_with_25pct_tolerance"]) for row in rows
        ),
        "adverse_within_tolerance": sum(
            bool(row["adverse_within_25pct_tolerance"]) for row in rows
        ),
        "rows": rows,
    }


def duration_cross_check(trades: Sequence[OwnerTrade]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for trade in trades:
        if trade.duration_bars is None:
            continue
        elapsed = (trade.exit_time - trade.entry_time).total_seconds() / 60.0
        expected = trade.duration_bars * 3
        if elapsed != expected:
            rows.append(
                {
                    "trade": trade.number,
                    "elapsed_minutes": elapsed,
                    "duration_bars": trade.duration_bars,
                    "expected_minutes": expected,
                }
            )
    return {"trades": len(trades), "misfits": rows}


def run(lake: Path, owner_file: Path) -> dict[str, object]:
    trades = read_owner_trades(owner_file)
    if len(trades) != 31:
        raise RuntimeError(f"expected 31 owner trades, found {len(trades)}")
    start = min(item.entry_time for item in trades) - timedelta(days=3)
    end = max(item.exit_time for item in trades) + timedelta(days=1)
    minutes = read_nas100_minutes(lake, start, end)
    if not minutes:
        raise RuntimeError("NSXUSD 1m data is missing")
    results, values_by_shift = scan_moves(trades, minutes, SHIFT_MINUTES)
    best = _best(results)
    p_value = permutation_p_value(trades, values_by_shift, best.tolerance_matches)
    bars_3m = tuple(item for item in resample_ohlc(minutes, 3) if item.complete)
    open_results, _ = scan_moves(trades, bars_3m, tuple(range(-360, 361, 3)))
    close_results, _ = scan_moves(trades, bars_3m, tuple(range(-360, 361, 3)), close_label=True)
    best_open, best_close = _best(open_results), _best(close_results)
    return {
        "task": "T-165",
        "generated_at": datetime.now(UTC).isoformat(),
        "owner_entries": len(trades),
        "owner_timezone": str(OWNER_ZONE),
        "point_value_usd_inferred": POINT_VALUE_USD,
        "shift_grid": {"minimum": -360, "maximum": 360, "step": 1},
        "best_alignment": {
            "result": asdict(best),
            "permutation_p_value": p_value,
            "zero_shift": best.shift_minutes == 0,
        },
        "alignment_results": [asdict(item) for item in results],
        "bar_open_3m": {
            "best": asdict(best_open),
            "results": [asdict(item) for item in open_results],
        },
        "bar_close_3m": {
            "best": asdict(best_close),
            "results": [asdict(item) for item in close_results],
        },
        "excursions": {
            "shift_zero": excursion_summary(trades, minutes, 0),
            "shift_best": excursion_summary(trades, minutes, best.shift_minutes),
        },
        "duration_cross_check": duration_cross_check(trades),
        "coverage": {
            "NSXUSD": {
                "1m": len(minutes),
                "3m": len(bars_3m),
                "5m": len(tuple(item for item in resample_ohlc(minutes, 5) if item.complete)),
                "60m": len(tuple(item for item in resample_ohlc(minutes, 60) if item.complete)),
            },
            "XAUUSD": {"owner_rows": 0, "note": "no owner rows in this window"},
        },
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
    best = data["best_alignment"]["result"]
    p_value = data["best_alignment"]["permutation_p_value"]
    if p_value < 0.01 and best["tolerance_matches"] >= 20:
        verdict = f"Within-trade moves align at {best['shift_minutes']} minutes ({best['tolerance_matches']}/31 within tolerance, p={p_value:.4f}); a corrected replay may be justified."  # noqa: E501
    else:
        verdict = f"Within-trade moves do not align safely (best shift {best['shift_minutes']} minutes, {best['tolerance_matches']}/31 within tolerance, p={p_value:.4f}); request chart-bar CSV or Pine Inputs/Style screenshot."  # noqa: E501
    lines = [
        "# T-165 within-trade price-move alignment",
        "",
        "**Verdict:** " + verdict,
        "",
        "Diagnostic-only offline replay; no broker calls, orders, holdout reads, lake writes, production writes or tuning.",  # noqa: E501
        "",
        "## Best 1m alignment",
        "",
        json.dumps(data["best_alignment"], indent=2),
        "",
        "## 3m bar-open and bar-close cross-check",
        "",
        json.dumps(
            {"bar_open": data["bar_open_3m"]["best"], "bar_close": data["bar_close_3m"]["best"]},
            indent=2,
        ),
        "",
        "## Excursions",
        "",
        json.dumps(
            {
                "shift_zero": data["excursions"]["shift_zero"],
                "shift_best": data["excursions"]["shift_best"],
            },
            indent=2,
        ),
        "",
        "## Duration cross-check",
        "",
        json.dumps(data["duration_cross_check"], indent=2),
        "",
        "## Coverage",
        "",
        json.dumps(data["coverage"], indent=2),
        "",
        "## Safety",
        "",
        json.dumps(data["safety"], indent=2),
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--trades", type=Path, required=True)
    args = parser.parse_args()
    data = run(args.lake, args.owner_file)
    args.output.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    trades = read_owner_trades(args.owner_file)
    with args.trades.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(trades[0]).keys()) + ["signed_move"])
        writer.writeheader()
        for trade in trades:
            writer.writerow({**asdict(trade), "signed_move": trade.signed_move})
    args.report.write_text(report(data), encoding="utf-8")
    print(
        json.dumps(
            {"task": "T-165", "best": data["best_alignment"], "safety": data["safety"]},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
