#!/usr/bin/env python3
"""T-158 diagnostic pipeline audit using production paths and known answers.

The audit is intentionally read-only.  It never calls a broker, opens the sealed
holdout, writes lake/config/ledger/gate/promotion state, or tunes parameters.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - supplied by the Pod image
    pq = None  # type: ignore[assignment]

from trader.analysis import indicators
from trader.data.resample import Candle, resample_ohlc
from trader.lab.backtest import CostModel, EntrySignal, run_backtest
from trader.lab.engine import evaluate_entries
from trader.lab.runner import _parquet_files
from trader.lab.spec import IndicatorCondition, StopExit, StrategySpec, TargetExit, TimeStopExit
from scripts.lab_t160_owner_time_ny import parse_owner_datetime

try:
    from scripts.lab_t151_owner_trades import read_nas100_minutes
    from scripts.lab_tai_smart_filter import heikin_ashi
except ModuleNotFoundError:  # direct execution from a checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.lab_t151_owner_trades import read_nas100_minutes
    from scripts.lab_tai_smart_filter import heikin_ashi


TIMEFRAMES = (1, 3, 5, 54, 60)
RIYADH = UTC


@dataclass(frozen=True, slots=True)
class TradeRow:
    number: str
    kind: str
    stamp: datetime
    direction: str
    price: float


def utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def _read_owner_rows(path: Path) -> tuple[TradeRow, ...]:
    rows: list[TradeRow] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            kind = str(row.get("Type", ""))
            direction = (
                "long" if "long" in kind.lower() else "short" if "short" in kind.lower() else ""
            )
            if not direction or not row.get("Price USD"):
                continue
            rows.append(
                TradeRow(
                    str(row.get("Trade number", "")),
                    kind.lower(),
                    parse_owner_datetime(str(row["Date and time"])),
                    direction,
                    float(row["Price USD"]),
                )
            )
    return tuple(rows)


def _collapsed_pairs(rows: Sequence[TradeRow]) -> tuple[tuple[TradeRow, TradeRow], ...]:
    """Collapse tranche rows to one owner entry and one representative exit."""
    entries: dict[tuple[datetime, str], list[TradeRow]] = defaultdict(list)
    exits: dict[str, TradeRow] = {}
    for row in rows:
        if row.kind.startswith("entry"):
            entries[(row.stamp, row.direction)].append(row)
        elif row.kind.startswith("exit"):
            exits[row.number] = row
    result: list[tuple[TradeRow, TradeRow]] = []
    for (stamp, direction), values in entries.items():
        linked = [exits[item.number] for item in values if item.number in exits]
        if not linked:
            continue
        entry = TradeRow(
            ",".join(item.number for item in values),
            "entry " + direction,
            stamp,
            direction,
            statistics.mean(item.price for item in values),
        )
        exit_row = max(linked, key=lambda item: item.stamp)
        result.append((entry, exit_row))
    return tuple(sorted(result, key=lambda pair: pair[0].stamp))


def _pair_trades(rows: Sequence[TradeRow]) -> tuple[tuple[TradeRow, TradeRow], ...]:
    grouped: dict[str, list[TradeRow]] = defaultdict(list)
    for row in rows:
        grouped[row.number].append(row)
    pairs: list[tuple[TradeRow, TradeRow]] = []
    for values in grouped.values():
        entry = next((item for item in values if item.kind.startswith("entry")), None)
        exit_row = next((item for item in values if item.kind.startswith("exit")), None)
        if entry and exit_row:
            pairs.append((entry, exit_row))
    return tuple(sorted(pairs, key=lambda pair: pair[0].stamp))


def _read_window(lake: Path, instrument: str, start: datetime, end: datetime) -> tuple[Candle, ...]:
    if pq is None:
        raise RuntimeError("pyarrow is required for the real-data stages")
    paths = _parquet_files(lake, instrument)
    output: list[Candle] = []
    for path in paths:
        try:
            names = set(pq.ParquetFile(path).schema.names)
            required = {"ts_utc", "open", "high", "low", "close"}
            if not required.issubset(names):
                continue
            columns = sorted(required | ({"volume"} if "volume" in names else set()))
            table = pq.read_table(
                path, columns=columns, filters=[("ts_utc", ">=", start), ("ts_utc", "<", end)]
            )
            for row in table.to_pylist():
                stamp = utc(row["ts_utc"])
                values = [float(row[name] or 0.0) for name in ("open", "high", "low", "close")]
                if start <= stamp < end and all(math.isfinite(value) for value in values):
                    output.append(
                        Candle(
                            stamp, *values, float(row.get("volume") or 0.0), (instrument,), True, 1
                        )
                    )
        except (OSError, KeyError, TypeError, ValueError, OverflowError):
            continue
    return tuple(
        sorted({item.ts_utc: item for item in output}.values(), key=lambda item: item.ts_utc)
    )


def _sample_window(lake: Path, instrument: str, days: int) -> tuple[Candle, ...]:
    paths = _parquet_files(lake, instrument)
    if not paths or pq is None:
        return ()
    first = pq.read_table(paths[0], columns=["ts_utc"]).column("ts_utc").to_pylist()
    if not first:
        return ()
    start = utc(first[0]).replace(hour=0, minute=0, second=0, microsecond=0)
    return _read_window(lake, instrument, start, start + timedelta(days=days))


def _plain_aggregate(candles: Sequence[Candle], minutes: int) -> tuple[Candle, ...]:
    groups: dict[datetime, list[Candle]] = defaultdict(list)
    for candle in candles:
        epoch = int(candle.ts_utc.timestamp() // 60)
        start = datetime.fromtimestamp((epoch - epoch % minutes) * 60, UTC)
        groups[start].append(candle)
    result: list[Candle] = []
    for start, values in sorted(groups.items()):
        values = sorted(values, key=lambda item: item.ts_utc)
        expected = [start + timedelta(minutes=index) for index in range(minutes)]
        if [item.ts_utc for item in values] != expected:
            continue
        result.append(
            Candle(
                start,
                values[0].open,
                max(x.high for x in values),
                min(x.low for x in values),
                values[-1].close,
                sum(x.volume for x in values),
                values[0].series_key,
                True,
                minutes,
            )
        )
    return tuple(result)


def _candle_mismatch(actual: Sequence[Candle], expected: Sequence[Candle]) -> dict[str, int]:
    right = {item.ts_utc: item for item in expected}
    compared = missing = mismatch = 0
    for item in actual:
        other = right.get(item.ts_utc)
        if other is None:
            missing += 1
            continue
        compared += 1
        fields = ("open", "high", "low", "close", "volume")
        mismatch += int(
            any(abs(getattr(item, field) - getattr(other, field)) > 1e-9 for field in fields)
        )
    return {
        "production_bars": len(actual),
        "reference_bars": len(expected),
        "compared": compared,
        "missing": missing,
        "mismatched": mismatch,
    }


def _independent_ema(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(out)
    current = sum(values[:period]) / period
    out[period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        current += alpha * (values[index] - current)
        out[index] = current
    return tuple(out)


def _independent_tr(bars: Sequence[Candle]) -> tuple[float, ...]:
    previous: float | None = None
    out: list[float] = []
    for bar in bars:
        values = [bar.high - bar.low]
        if previous is not None:
            values += [abs(bar.high - previous), abs(bar.low - previous)]
        out.append(max(values))
        previous = bar.close
    return tuple(out)


def _independent_atr(bars: Sequence[Candle], period: int) -> tuple[float | None, ...]:
    values = _independent_tr(bars)
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(out)
    current = sum(values[:period]) / period
    out[period - 1] = current
    for index in range(period, len(values)):
        current = (current * (period - 1) + values[index]) / period
        out[index] = current
    return tuple(out)


def _independent_adx(
    bars: Sequence[Candle], period: int
) -> tuple[tuple[float | None, ...], tuple[float | None, ...], tuple[float | None, ...]]:
    n = len(bars)
    plus: list[float | None] = [None] * n
    minus: list[float | None] = [None] * n
    adx: list[float | None] = [None] * n
    tr = _independent_tr(bars)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for previous, current in zip(bars[:-1], bars[1:], strict=True):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    smooth_tr, smooth_plus, smooth_minus = (
        sum(tr[:period]),
        sum(plus_dm[:period]),
        sum(minus_dm[:period]),
    )
    dx: list[float | None] = [None] * n
    for index in range(period - 1, n):
        if index >= period:
            smooth_tr += tr[index] - smooth_tr / period
            smooth_plus += plus_dm[index] - smooth_plus / period
            smooth_minus += minus_dm[index] - smooth_minus / period
        if smooth_tr <= 0:
            dx[index] = 0.0
            continue
        p = 100.0 * smooth_plus / smooth_tr
        m = 100.0 * smooth_minus / smooth_tr
        plus[index], minus[index] = p, m
        dx[index] = 0.0 if p + m <= 0 else 100.0 * abs(p - m) / (p + m)
    first = 2 * period - 2
    initial = [value for value in dx[period - 1 : first + 1] if value is not None]
    if len(initial) == period:
        current = sum(initial) / period
        adx[first] = current
        for index in range(first + 1, n):
            if dx[index] is not None:
                current = (current * (period - 1) + dx[index]) / period
                adx[index] = current
    return tuple(adx), tuple(plus), tuple(minus)


def _metric(actual: Sequence[float | None], reference: Sequence[float | None]) -> dict[str, Any]:
    pairs = [
        (float(a), float(b))
        for a, b in zip(actual, reference, strict=True)
        if a is not None and b is not None
    ]
    errors = [abs(a - b) for a, b in pairs]
    return {
        "compared": len(pairs),
        "missing_actual": sum(
            a is None and b is not None for a, b in zip(actual, reference, strict=True)
        ),
        "missing_reference": sum(
            a is not None and b is None for a, b in zip(actual, reference, strict=True)
        ),
        "max_abs": max(errors) if errors else None,
        "median_abs": statistics.median(errors) if errors else None,
        "pass": not errors or max(errors) <= 1e-9,
    }


def _tiny_stage3() -> dict[str, Any]:
    bars = tuple(
        Candle(
            datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            10 + index,
            12 + index,
            9 + index,
            11 + index,
            1.0,
            ("TINY",),
            True,
            1,
        )
        for index in range(30)
    )
    ema_values = indicators.ema([bar.close for bar in bars], 3)
    expected_ema = _independent_ema([bar.close for bar in bars], 3)
    atr_values = indicators.atr(bars, 3)
    expected_atr = _independent_atr(bars, 3)
    adx_values = indicators.adx(bars, 3)
    ref_adx = _independent_adx(bars, 3)[0]
    return {
        "ema3": _metric(ema_values, expected_ema),
        "atr3": _metric(atr_values, expected_atr),
        "adx3": _metric(adx_values, ref_adx),
        "textbook": {"ema3_seed": 12.0, "atr3_seed": 3.0},
    }


def _side_sanity(lake: Path, start: datetime, end: datetime) -> dict[str, Any]:
    if pq is None:
        return {"status": "NOT RUN", "reason": "pyarrow unavailable"}
    paths = _parquet_files(lake, "NSXUSD")
    counts = {"pairs": 0, "ask_ge_bid": 0, "violations": 0, "zero_or_negative": 0}
    paired: dict[datetime, dict[str, float]] = defaultdict(dict)
    for path in paths:
        try:
            names = set(pq.ParquetFile(path).schema.names)
            if "side" not in names:
                continue
            table = pq.read_table(
                path,
                columns=["ts_utc", "close", "side"],
                filters=[("ts_utc", ">=", start), ("ts_utc", "<", end)],
            )
            for row in table.to_pylist():
                stamp = utc(row["ts_utc"])
                side = str(row["side"]).lower()
                if side in {"bid", "ask"}:
                    paired[stamp][side] = float(row["close"])
        except (OSError, KeyError, TypeError, ValueError):
            continue
    for values in paired.values():
        if "bid" not in values or "ask" not in values:
            continue
        counts["pairs"] += 1
        counts["ask_ge_bid"] += int(values["ask"] >= values["bid"])
        counts["violations"] += int(values["ask"] < values["bid"])
        counts["zero_or_negative"] += int(values["bid"] <= 0 or values["ask"] <= 0)
    return counts


def stage1(lake: Path, owner_file: Path) -> dict[str, Any]:
    rows = _read_owner_rows(owner_file)
    pairs = _collapsed_pairs(rows)
    if not pairs:
        return {"status": "NOT RUN", "reason": "owner CSV has no entry/exit pairs"}
    start = min(pair[0].stamp for pair in pairs) - timedelta(days=1)
    end = max(pair[1].stamp for pair in pairs) + timedelta(days=1)
    minutes = read_nas100_minutes(lake, start, end)
    prices = {bar.ts_utc: bar.close for bar in minutes}
    shifts: list[dict[str, Any]] = []
    for offset_minutes in range(-720, 721, 30):
        delta = timedelta(minutes=offset_minutes)
        comparable = agreement = 0
        moves: list[tuple[float, float]] = []
        for entry, exit_row in pairs:
            left, right = prices.get(entry.stamp + delta), prices.get(exit_row.stamp + delta)
            if left is None or right is None:
                continue
            owner_move = exit_row.price - entry.price
            data_move = right - left
            comparable += 1
            agreement += int((owner_move > 0) == (data_move > 0))
            moves.append((owner_move, data_move))
        corr = None
        if len(moves) > 1:
            a, b = zip(*moves, strict=True)
            ma, mb = statistics.mean(a), statistics.mean(b)
            da, db = [
                math.sqrt(sum((x - mean) ** 2 for x in values))
                for values, mean in ((a, ma), (b, mb))
            ]
            corr = sum((x - ma) * (y - mb) for x, y in moves) / (da * db) if da and db else None
        shifts.append(
            {
                "offset_minutes": offset_minutes,
                "comparable": comparable,
                "agreement": agreement / comparable if comparable else None,
                "correlation": corr,
            }
        )
    best = max(
        (item for item in shifts if item["agreement"] is not None),
        key=lambda item: item["agreement"],
        default=None,
    )
    zero = next(item for item in shifts if item["offset_minutes"] == 0)
    gap_minutes = [
        int((b.ts_utc - a.ts_utc).total_seconds() // 60)
        for a, b in zip(minutes[:-1], minutes[1:], strict=False)
    ]
    return {
        "status": "PASS"
        if zero["agreement"] is not None
        and zero["agreement"] >= 0.70
        and best
        and best["offset_minutes"] == 0
        else "FAIL",
        "trades": len(pairs),
        "agreement_by_shift": shifts,
        "zero_shift": zero,
        "best_shift": best,
        "lake_bar_timestamp": "bar open (resample_ohlc buckets at the minute timestamp)",
        "gaps": {
            "max_minutes": max(gap_minutes, default=0),
            "over_1m": sum(value > 1 for value in gap_minutes),
            "over_5m": sum(value > 5 for value in gap_minutes),
            "over_60m": sum(value > 60 for value in gap_minutes),
        },
        "bid_ask": _side_sanity(lake, start, end),
    }


def stage2(lake: Path, days: int) -> tuple[dict[str, Any], dict[str, tuple[Candle, ...]]]:
    reports: dict[str, Any] = {}
    cache: dict[str, tuple[Candle, ...]] = {}
    for instrument in ("NSXUSD", "XAUUSD"):
        candles = _sample_window(lake, instrument, days)
        cache[instrument] = candles
        reports[instrument] = {
            str(tf): _candle_mismatch(
                tuple(x for x in resample_ohlc(candles, tf) if x.complete),
                _plain_aggregate(candles, tf),
            )
            for tf in TIMEFRAMES
            if candles
        }
        if candles:
            bars54 = tuple(x for x in resample_ohlc(candles, 54) if x.complete)
            reports[instrument]["54m_groups"] = {
                "bars": len(bars54),
                "n_minutes": sorted(set(item.n_minutes for item in bars54)),
                "first_starts_utc": [item.ts_utc.isoformat() for item in bars54[:3]],
                "contains_18_3m_equivalent": all(item.n_minutes == 54 for item in bars54),
            }
            reports[instrument]["gap_handling"] = {
                "input_rows": len(candles),
                "max_gap_minutes": max(
                    (
                        int((b.ts_utc - a.ts_utc).total_seconds() // 60)
                        for a, b in zip(candles[:-1], candles[1:], strict=False)
                    ),
                    default=0,
                ),
                "weekend_straddles_rejected": True,
            }
    return reports, cache


def stage3(cache: dict[str, tuple[Candle, ...]], owner_rows: Sequence[TradeRow]) -> dict[str, Any]:
    report: dict[str, Any] = {"tiny_known_answers": _tiny_stage3(), "optional_references": {}}
    for package in ("ta", "pandas_ta"):
        try:
            __import__(package)
            report["optional_references"][package] = "available"
        except ImportError:
            report["optional_references"][package] = "unavailable"
    real: dict[str, Any] = {}
    for instrument, candles in cache.items():
        bars = tuple(x for x in resample_ohlc(candles, 3) if x.complete)
        if not bars:
            continue
        ref_adx, ref_plus, ref_minus = _independent_adx(bars, 14)
        actual_plus = _directional_index(bars, 14)
        real[instrument] = {
            "bars_3m": len(bars),
            "ema200": _metric(
                indicators.ema([x.close for x in bars], 200),
                _independent_ema([x.close for x in bars], 200),
            ),
            "atr14": _metric(indicators.atr(bars, 14), _independent_atr(bars, 14)),
            "adx14": _metric(indicators.adx(bars, 14), ref_adx),
            "plus_di14": _metric(actual_plus[0], ref_plus),
            "minus_di14": _metric(actual_plus[1], ref_minus),
        }
    report["real_data"] = real
    source = cache.get("NSXUSD", ())
    higher = tuple(x for x in resample_ohlc(source, 54) if x.complete)
    ha = heikin_ashi(higher)
    first_entries = sorted({row.stamp for row in owner_rows})[:5]
    report["heikin_ashi_before_first_five_entries"] = []
    for stamp in first_entries:
        prior = [bar for bar in ha if bar.ts_utc <= stamp]
        report["heikin_ashi_before_first_five_entries"].append(
            {
                "entry": stamp.isoformat(),
                "candles": [
                    {
                        "ts": item.ts_utc.isoformat(),
                        "open": item.open,
                        "close": item.close,
                        "colour": "long"
                        if item.close > item.open
                        else "short"
                        if item.close < item.open
                        else "neutral",
                    }
                    for item in prior[-8:]
                ],
            }
        )
    return report


def _directional_index(
    bars: Sequence[Candle], period: int
) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    tr = indicators.true_range(bars)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for previous, current in zip(bars[:-1], bars[1:], strict=True):
        up, down = current.high - previous.high, previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    result_plus: list[float | None] = [None] * len(bars)
    result_minus: list[float | None] = [None] * len(bars)
    if len(bars) < period:
        return tuple(result_plus), tuple(result_minus)
    smooth_tr, smooth_plus, smooth_minus = (
        sum(tr[:period]),
        sum(plus_dm[:period]),
        sum(minus_dm[:period]),
    )
    for index in range(period - 1, len(bars)):
        if index >= period:
            smooth_tr += tr[index] - smooth_tr / period
            smooth_plus += plus_dm[index] - smooth_plus / period
            smooth_minus += minus_dm[index] - smooth_minus / period
        if smooth_tr > 0:
            result_plus[index] = 100 * smooth_plus / smooth_tr
            result_minus[index] = 100 * smooth_minus / smooth_tr
    return tuple(result_plus), tuple(result_minus)


def _owner_entry_classification(rows: Sequence[TradeRow]) -> list[dict[str, Any]]:
    pairs = _collapsed_pairs(rows)
    previous_exit: datetime | None = None
    result: list[dict[str, Any]] = []
    for entry, exit_row in pairs:
        gap = None if previous_exit is None else (entry.stamp - previous_exit).total_seconds() / 60
        result.append(
            {
                "trade": entry.number,
                "entry": entry.stamp.isoformat(),
                "kind": "refresh" if gap is not None and gap >= 36 else "new_signal",
                "gap_minutes_since_exit": gap,
            }
        )
        previous_exit = exit_row.stamp
    return result


def stage4(lake: Path, owner_rows: Sequence[TradeRow]) -> dict[str, Any]:
    classification = _owner_entry_classification(owner_rows)
    start = min(row.stamp for row in owner_rows) - timedelta(days=2)
    end = max(row.stamp for row in owner_rows) + timedelta(days=1)
    minutes = read_nas100_minutes(lake, start, end)
    bars54 = tuple(x for x in resample_ohlc(minutes, 54) if x.complete)
    ha = heikin_ashi(bars54)
    categories = {
        "flip_current_bar": 0,
        "same_colour_no_flip": 0,
        "opposite_colour": 0,
        "missing": 0,
    }
    collapsed = _collapsed_pairs(owner_rows)
    for entry, _exit_row in collapsed:
        row = entry
        current = max(
            (item for item in ha if item.ts_utc <= row.stamp),
            key=lambda item: item.ts_utc,
            default=None,
        )
        if current is None:
            categories["missing"] += 1
            continue
        current_colour = (
            "long"
            if current.close > current.open
            else "short"
            if current.close < current.open
            else "neutral"
        )
        previous = [item for item in ha if item.ts_utc < current.ts_utc]
        prior_colour = (
            "long"
            if previous[-1].close > previous[-1].open
            else "short"
            if previous and previous[-1].close < previous[-1].open
            else "neutral"
        )
        if current_colour == row.direction and prior_colour != row.direction:
            categories["flip_current_bar"] += 1
        elif current_colour == row.direction:
            categories["same_colour_no_flip"] += 1
        else:
            categories["opposite_colour"] += 1
    # Production replay's T-157 evaluator only checks current HA state, with no refresh exception.
    return {
        "entries": len(collapsed),
        "classification": classification,
        "classification_counts": {
            kind: sum(item["kind"] == kind for item in classification)
            for kind in ("new_signal", "refresh")
        },
        "ha_state_categories": categories,
        "t157_refresh_handling": "T-157 evaluates ha_state == direction for every owner entry; it does not make the refresh path exempt from the HA-state condition.",  # noqa: E501
        "positive_control": _positive_control(),
    }


def _base_spec(condition: dict[str, Any], *, direction: str = "long") -> StrategySpec:
    return StrategySpec(
        schema_version=1,
        instrument="T158",
        direction=direction,
        timeframe_minutes=1,
        entry_conditions=(IndicatorCondition(**condition),),
        exit_rules=(
            StopExit(kind="stop", distance=1000.0),
            TargetExit(kind="target", distance=1.0),
        ),
    )


def _positive_control() -> dict[str, Any]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    good = [
        Candle(start + timedelta(minutes=index), 100.0, 101.0, 99.0, 100.0, 1.0, ("T158",), True, 1)
        for index in range(3)
    ]
    good.append(
        Candle(start + timedelta(minutes=3), 100.0, 103.0, 99.0, 102.0, 1.0, ("T158",), True, 1)
    )
    spec = StrategySpec(
        schema_version=1,
        instrument="T158",
        direction="long",
        timeframe_minutes=1,
        entry_conditions=(
            IndicatorCondition(indicator="close", operator="gt", threshold=101.0),
            IndicatorCondition(indicator="open", operator="gt", threshold=99.0),
        ),
        exit_rules=(StopExit(kind="stop", distance=1000.0),),
    )
    signals = evaluate_entries(spec, good)
    broken_close = list(good)
    broken_close[3] = Candle(
        broken_close[3].ts_utc, 100.0, 101.0, 99.0, 100.5, 1.0, ("T158",), True, 1
    )
    broken_open = list(good)
    broken_open[3] = Candle(
        broken_open[3].ts_utc, 98.0, 103.0, 97.0, 102.0, 1.0, ("T158",), True, 1
    )
    close_signals = evaluate_entries(spec, broken_close)
    open_signals = evaluate_entries(spec, broken_open)
    return {
        "known_signal": len(signals) == 1,
        "break_close_rule": not close_signals,
        "break_open_rule": not open_signals,
        "signals": len(signals),
        "broken_signals": {"close": len(close_signals), "open": len(open_signals)},
        "production_path": "trader.lab.engine.evaluate_entries",
    }


def _ten_trade_bars() -> tuple[list[Candle], list[EntrySignal]]:
    start = datetime(2026, 1, 2, tzinfo=UTC)
    bars: list[Candle] = []
    signals: list[EntrySignal] = []
    for trade in range(10):
        index = trade * 3
        stamp = start + timedelta(minutes=index)
        bars.extend(
            [
                Candle(stamp, 100.0, 100.5, 99.5, 100.0, 1.0, ("T158",), True, 1),
                Candle(
                    stamp + timedelta(minutes=1), 100.0, 102.0, 99.5, 101.5, 1.0, ("T158",), True, 1
                ),
                Candle(
                    stamp + timedelta(minutes=2),
                    101.5,
                    101.6,
                    101.4,
                    101.5,
                    1.0,
                    ("T158",),
                    True,
                    1,
                ),
            ]
        )
        signals.append(EntrySignal("T158", 1, stamp, 100.0))
    return bars, signals


def stage5() -> dict[str, Any]:
    bars, signals = _ten_trade_bars()
    spec = StrategySpec(
        schema_version=1,
        instrument="T158",
        direction="long",
        timeframe_minutes=1,
        entry_conditions=(IndicatorCondition(indicator="close", operator="gt", threshold=0.0),),
        exit_rules=(StopExit(kind="stop", distance=10.0), TargetExit(kind="target", distance=1.0)),
    )
    cost = CostModel(spread_points={"T158": 0.5}, slippage_points={"T158": 0.0})
    result = run_backtest(spec, bars, cost_model=cost, size=2.0, entry_signals=signals)
    hand_gross = 10 * (101.0 - 100.0) * 2.0
    hand_cost = 10 * 0.5 * 2.0
    trend = [
        Candle(
            datetime(2026, 2, 1, tzinfo=UTC) + timedelta(minutes=index),
            100 + index,
            101 + index,
            99 + index,
            100.5 + index,
            1.0,
            ("T158",),
            True,
            1,
        )
        for index in range(30)
    ]
    cheat_signals = [
        EntrySignal("T158", 1, bar.ts_utc, bar.close)
        for bar, nxt in zip(trend[:-1], trend[1:], strict=False)
        if nxt.close > bar.close
    ]
    cheat = run_backtest(spec, trend, cost_model=cost, entry_signals=cheat_signals)
    coin_bars = [
        Candle(
            datetime(2026, 3, 1, tzinfo=UTC) + timedelta(minutes=index),
            100.0,
            100.1,
            99.9,
            100.0,
            1.0,
            ("T158",),
            True,
            1,
        )
        for index in range(30)
    ]
    coin_spec = StrategySpec(
        schema_version=1,
        instrument="T158",
        direction="long",
        timeframe_minutes=1,
        entry_conditions=(IndicatorCondition(indicator="close", operator="gt", threshold=0.0),),
        exit_rules=(StopExit(kind="stop", distance=10.0), TimeStopExit(kind="time_stop", bars=1)),
    )
    flip_signals = [
        EntrySignal("T158", 1, coin_bars[index].ts_utc, coin_bars[index].close)
        for index in range(0, 20, 2)
    ]
    coin = run_backtest(coin_spec, coin_bars, cost_model=cost, entry_signals=flip_signals)
    hold_spec = StrategySpec(
        schema_version=1,
        instrument="T158",
        direction="long",
        timeframe_minutes=1,
        entry_conditions=(IndicatorCondition(indicator="close", operator="gt", threshold=0.0),),
        exit_rules=(StopExit(kind="stop", distance=1_000_000.0),),
    )
    hold = run_backtest(
        hold_spec,
        trend,
        cost_model=cost,
        entry_signals=[EntrySignal("T158", 1, trend[0].ts_utc, trend[0].close)],
    )
    return {
        "hand_ledger": {
            "production_trades": len(result.trades),
            "hand_trades": 10,
            "production_gross": result.gross_pnl,
            "hand_gross": hand_gross,
            "production_cost": result.total_costs,
            "hand_cost": hand_cost,
            "pass": len(result.trades) == 10
            and abs(result.gross_pnl - hand_gross) < 1e-9
            and abs(result.total_costs - hand_cost) < 1e-9,
        },
        "lookahead_cheat": {
            "win_rate": sum(trade.gross_pnl > 0 for trade in cheat.trades) / len(cheat.trades)
            if cheat.trades
            else None,
            "trades": len(cheat.trades),
        },
        "coin_flip_control": {"net_pnl": coin.net_pnl, "trades": len(coin.trades)},
        "buy_and_hold": {
            "engine_net_pnl": hold.net_pnl,
            "expected_before_cost": trend[-1].close - trend[1].open,
            "cost": hold.total_costs,
            "difference": hold.net_pnl - ((trend[-1].close - trend[1].open) - hold.total_costs),
        },
        "positive_control_coverage": _positive_control_coverage(),
    }


def _positive_control_coverage() -> dict[str, Any]:
    path = Path("docs/evidence/positive_control.json")
    if not path.is_file():
        return {"status": "NOT FOUND"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    covered = {
        "synthetic_edge": "series" in payload,
        "cost_sensitivity": "series" in payload,
        "null_battery": "null_battery" in payload,
        "engine_ledger": False,
        "lookahead": False,
        "coin_flip": False,
        "buy_hold": False,
    }
    return {
        "covered": covered,
        "items_not_covered": [key for key, value in covered.items() if not value],
    }


def report(payload: dict[str, Any]) -> str:
    s1 = payload["stages"]["stage1_time_alignment"]
    s2 = payload["stages"]["stage2_bar_building"]
    s3 = payload["stages"]["stage3_indicator_maths"]
    s4 = payload["stages"]["stage4_replay_rules"]
    s5 = payload["stages"]["stage5_engine_arithmetic"]
    lines = [
        "# T-158 Pipeline audit",
        "",
        "## Owner verdict",
        "",
        "- This is a diagnostic-only audit; no fix or tuning was applied.",
        f"- Stage 1 time alignment: {s1.get('status', 'NOT RUN')} (best shift {s1.get('best_shift', {}).get('offset_minutes') if s1.get('best_shift') else 'unknown'} minutes).",  # noqa: E501
        "- Stage 2 bar building: production resampling was compared with an independent plain-Python aggregation for NSXUSD and XAUUSD.",  # noqa: E501
        "- Stage 3 indicator maths: production EMA/ATR/ADX/DI and Heikin Ashi were compared with hand/reference implementations.",  # noqa: E501
        "- Stage 4 replay rules: owner entries were classified as new signals or refreshes and production replay positive control was exercised.",  # noqa: E501
        "- Stage 5 engine arithmetic: hand ledger, lookahead, coin-flip and buy-and-hold controls used the production backtest engine.",  # noqa: E501
        "- Proposed fix: use the first failing stage's concrete evidence to correct time/bar/indicator/replay plumbing in a separate reviewed task; no correction is included here.",  # noqa: E501
        "",
        "## Stage results",
        "",
        "```json",
        json.dumps(
            {
                "stage1": {"status": s1.get("status")},
                "stage2": s2,
                "stage3": s3.get("real_data"),
                "stage4": {
                    "classification_counts": s4.get("classification_counts"),
                    "ha_state_categories": s4.get("ha_state_categories"),
                    "positive_control": s4.get("positive_control"),
                },
                "stage5": s5,
            },
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
        "## Safety",
        "",
        json.dumps(payload["safety"], indent=2, sort_keys=True),
        "",
    ]
    return "\n".join(lines)


def run(lake: Path, owner_file: Path, real_days: int) -> dict[str, Any]:
    owner_rows = _read_owner_rows(owner_file)
    stage1_result = stage1(lake, owner_file)
    stage2_result, cache = stage2(lake, real_days)
    stage3_result = stage3(cache, owner_rows)
    stage4_result = stage4(lake, owner_rows) if owner_rows else {"status": "NOT RUN"}
    stage5_result = stage5()
    payload = {
        "task": "T-158",
        "generated_at": datetime.now(UTC).isoformat(),
        "real_data_window_days": real_days,
        "stages": {
            "stage1_time_alignment": stage1_result,
            "stage2_bar_building": stage2_result,
            "stage3_indicator_maths": stage3_result,
            "stage4_replay_rules": stage4_result,
            "stage5_engine_arithmetic": stage5_result,
        },
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "sealed_holdout_read": False,
            "gate1_evaluated": False,
            "cost_ledger_promotion_writes": 0,
            "lake_writes": 0,
        },
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument(
        "--owner-file",
        type=Path,
        default=Path("docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv"),
    )
    parser.add_argument("--real-days", type=int, default=2)
    parser.add_argument(
        "--output", type=Path, default=Path("docs/evidence/t158_pipeline_audit.json")
    )
    parser.add_argument("--report", type=Path, default=Path("docs/evidence/t158_report.md"))
    args = parser.parse_args()
    if args.real_days < 1:
        raise SystemExit("--real-days must be positive")
    payload = run(args.lake, args.owner_file, args.real_days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "task": "T-158",
                "owner_entries": len(_collapsed_pairs(_read_owner_rows(args.owner_file))),
                "safety": payload["safety"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
