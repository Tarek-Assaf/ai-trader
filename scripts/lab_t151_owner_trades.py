#!/usr/bin/env python3
"""Compare the owner's TradingView entries with the Tai signal on forming candles.

This is a read-only diagnostic.  It reads the supplied owner CSV and the
recorded NAS100 lake, never calls a broker, reads the sealed holdout, or writes
gate, cost, ledger, or promotion state.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trader.analysis import indicators
from trader.data.resample import Candle

try:
    from scripts.lab_tai_smart_filter import (
        Features,
        TaiParams,
        _passes,
        _risk,
        directional_index,
        fallback_vwap,
    )
except ModuleNotFoundError:  # direct ``python scripts/lab_t151_owner_trades.py`` execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.lab_tai_smart_filter import (
        Features,
        TaiParams,
        _passes,
        _risk,
        directional_index,
        fallback_vwap,
    )


TIMEFRAME_MINUTES = 3
HIGHER_MINUTES = 54
HIGHER_BARS = HIGHER_MINUTES // TIMEFRAME_MINUTES
WARMUP_BARS = 200
TRIAL_COUNT = 9
ANCHORS = ("cme_open", "utc_midnight", "riyadh_midnight")
VARIANTS = ("closed", "forming_reset", "forming_carried", "refresh", "refresh_gated")
ENTRY_WINDOW = timedelta(minutes=5)
RIYADH = timezone(timedelta(hours=3))


@dataclass(frozen=True, slots=True)
class OwnerEntry:
    entry_time: datetime
    direction: str
    entry_price: float
    quantity: float
    stop_price: float | None = None


@dataclass(frozen=True, slots=True)
class Signal:
    time: datetime
    direction: str
    index: int


@dataclass(frozen=True, slots=True)
class MinuteRow:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default), encoding="utf-8"
    )
    os.replace(temporary, path)


def read_owner_entries(path: Path) -> tuple[OwnerEntry, ...]:
    """Collapse tranche rows into one position per UTC entry and direction."""
    grouped: dict[tuple[datetime, str], list[tuple[float, float, str]]] = defaultdict(list)
    trade_stops: dict[str, list[float]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            kind = str(row.get("Type", "")).lower()
            trade_number = str(row.get("Trade number", ""))
            if "stop" in str(row.get("Signal", "")).lower() and row.get("Price USD"):
                try:
                    trade_stops[trade_number].append(float(row["Price USD"]))
                except ValueError:
                    pass
            if not kind.startswith("entry"):
                continue
            direction = "long" if "long" in kind else "short" if "short" in kind else ""
            if not direction:
                continue
            stamp = datetime.strptime(str(row["Date and time"]), "%Y-%m-%d %H:%M").replace(
                tzinfo=UTC
            )
            price = float(row["Price USD"])
            quantity = abs(float(row.get("Size (qty)") or 0.0))
            grouped[(stamp, direction)].append((price, quantity, trade_number))
    entries: list[OwnerEntry] = []
    for (stamp, direction), values in sorted(grouped.items()):
        quantity = sum(item[1] for item in values)
        price = (
            sum(item[0] * item[1] for item in values) / quantity
            if quantity > 0
            else sum(item[0] for item in values) / len(values)
        )
        stops = [stop for item in values for stop in trade_stops.get(item[2], [])]
        stop_price = sum(stops) / len(stops) if stops else None
        entries.append(OwnerEntry(stamp, direction, price, quantity, stop_price))
    return tuple(entries)


def _candidate_paths(lake: Path) -> tuple[Path, ...]:
    """Find the Capital.com NAS100 1m tree without assuming one historical alias."""
    roots = (
        lake / "NAS100" / "1m_capital",
        lake / "NAS100" / "1m",
        lake / "US100" / "1m_capital",
        lake / "US100" / "1m",
        lake / "NAS100" / "1m_capitalcom",
    )
    files: set[Path] = set()
    for root in roots:
        if root.is_dir():
            files.update(root.rglob("*.parquet"))
    return tuple(sorted(files))


def _row_to_minute(row: dict[str, Any], side: str) -> MinuteRow:
    stamp = _utc(row["ts_utc"])
    return MinuteRow(
        stamp,
        float(row["open"]),
        float(row["high"]),
        float(row["low"]),
        float(row["close"]),
    )


def read_nas100_minutes(lake: Path, start: datetime, end: datetime) -> tuple[Candle, ...]:
    """Read paired bid/ask Capital.com rows and return mid-price 1m candles."""
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency is in the project image
        raise RuntimeError("pyarrow is required for T-151") from exc
    paired: dict[datetime, dict[str, MinuteRow]] = defaultdict(dict)
    singles: list[MinuteRow] = []
    for path in _candidate_paths(lake):
        try:
            available = set(pq.ParquetFile(path).schema.names)
            required = {"ts_utc", "open", "high", "low", "close"}
            if not required.issubset(available):
                continue
            columns = sorted(required | ({"side"} if "side" in available else set()))
            table = pq.read_table(path, columns=columns)
            rows = table.to_pylist()
            for row in rows:
                stamp = _utc(row["ts_utc"])
                if not start <= stamp < end:
                    continue
                side = str(row.get("side", "")).lower()
                item = _row_to_minute(row, side)
                if side in {"bid", "ask"}:
                    paired[stamp][side] = item
                else:
                    singles.append(item)
        except (OSError, KeyError, TypeError, ValueError, OverflowError):
            continue
    result: list[Candle] = []
    if paired:
        for stamp in sorted(paired):
            sides = paired[stamp]
            if "bid" not in sides or "ask" not in sides:
                continue
            bid, ask = sides["bid"], sides["ask"]
            result.append(
                Candle(
                    stamp,
                    (bid.open + ask.open) / 2.0,
                    (bid.high + ask.high) / 2.0,
                    (bid.low + ask.low) / 2.0,
                    (bid.close + ask.close) / 2.0,
                    0.0,
                    ("NAS100", "mid"),
                    True,
                    1,
                )
            )
    else:
        result = [
            Candle(
                item.timestamp,
                item.open,
                item.high,
                item.low,
                item.close,
                0.0,
                ("NAS100", "mid"),
                True,
                1,
            )
            for item in sorted(singles, key=lambda item: item.timestamp)
        ]
    unique: dict[datetime, Candle] = {item.ts_utc: item for item in result}
    return tuple(unique[key] for key in sorted(unique))


def aggregate_3m(minutes: Sequence[Candle]) -> tuple[Candle, ...]:
    """Build UTC clock 3m bars and retain incomplete bars for diagnostics."""
    buckets: dict[datetime, list[Candle]] = defaultdict(list)
    for item in minutes:
        minute = item.ts_utc.replace(second=0, microsecond=0)
        start = minute - timedelta(minutes=minute.minute % TIMEFRAME_MINUTES)
        buckets[start].append(item)
    bars: list[Candle] = []
    for start, members in sorted(buckets.items()):
        members.sort(key=lambda item: item.ts_utc)
        complete = len(members) == 3 and all(
            later.ts_utc - earlier.ts_utc == timedelta(minutes=1)
            for earlier, later in zip(members[:-1], members[1:], strict=True)
        )
        bars.append(
            Candle(
                start,
                members[0].open,
                max(item.high for item in members),
                min(item.low for item in members),
                members[-1].close,
                0.0,
                ("NAS100", "mid"),
                complete,
                len(members),
            )
        )
    return tuple(bars)


def _anchor_start(stamp: datetime, anchor: str) -> datetime:
    if anchor == "utc_midnight":
        return stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    if anchor == "riyadh_midnight":
        local = stamp.astimezone(RIYADH)
        return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    if anchor != "cme_open":
        raise ValueError(f"unknown anchor: {anchor}")
    chicago = ZoneInfo("America/Chicago")
    local = stamp.astimezone(chicago)
    opening = datetime(local.year, local.month, local.day, 17, tzinfo=chicago)
    if stamp < opening.astimezone(UTC):
        opening -= timedelta(days=1)
    return opening.astimezone(UTC)


def _higher_groups(chart: Sequence[Candle], anchor: str) -> tuple[tuple[Candle, ...], ...]:
    grouped: dict[datetime, list[Candle]] = defaultdict(list)
    for bar in chart:
        start = _anchor_start(bar.ts_utc, anchor)
        offset = int((bar.ts_utc - start).total_seconds() // 60)
        bucket = start + timedelta(minutes=HIGHER_MINUTES * (offset // HIGHER_MINUTES))
        grouped[bucket].append(bar)
    valid: list[tuple[Candle, ...]] = []
    for members in sorted(grouped.values(), key=lambda group: group[0].ts_utc):
        ordered = tuple(sorted(members, key=lambda item: item.ts_utc))
        if len(ordered) != HIGHER_BARS or not all(item.complete for item in ordered):
            continue
        if not all(
            later.ts_utc - earlier.ts_utc == timedelta(minutes=TIMEFRAME_MINUTES)
            for earlier, later in zip(ordered[:-1], ordered[1:], strict=True)
        ):
            continue
        valid.append(ordered)
    return tuple(valid)


def forming_heikin_ashi(
    bars: Sequence[Candle], previous: tuple[float, float] | None = None
) -> tuple[float, float]:
    """Return HA open/close for a partial higher-timeframe candle."""
    if not bars:
        raise ValueError("at least one source bar is required")
    close = (
        bars[0].open
        + max(item.high for item in bars)
        + min(item.low for item in bars)
        + bars[-1].close
    ) / 4.0
    open_price = (
        (bars[0].open + bars[0].close) / 2.0
        if previous is None
        else (previous[0] + previous[1]) / 2.0
    )
    return open_price, close


def _cross(previous: tuple[float, float], current: tuple[float, float]) -> str | None:
    if previous[1] <= previous[0] and current[1] > current[0]:
        return "long"
    if previous[1] >= previous[0] and current[1] < current[0]:
        return "short"
    return None


def _variant_candidates(chart: Sequence[Candle], anchor: str, variant: str) -> tuple[Signal, ...]:
    if variant == "refresh":
        return _refresh_candidates(tuple(chart), gated=False)
    if variant == "refresh_gated":
        return _refresh_candidates(tuple(chart), gated=True)
    groups = _higher_groups(chart, anchor)
    if variant == "closed":
        closed: list[tuple[Candle, tuple[float, float]]] = []
        previous: tuple[float, float] | None = None
        for group in groups:
            higher = Candle(
                group[0].ts_utc,
                group[0].open,
                max(item.high for item in group),
                min(item.low for item in group),
                group[-1].close,
                0.0,
                ("NAS100", "54m"),
                True,
                HIGHER_BARS,
            )
            current = forming_heikin_ashi(group, previous)
            closed.append((higher, current))
            previous = current
        result: list[Signal] = []
        previous_start: datetime | None = None
        for (_, previous_ha), (current_bar, current_ha) in zip(
            closed[:-1], closed[1:], strict=True
        ):
            if previous_start is not None and current_bar.ts_utc - previous_start != timedelta(
                minutes=HIGHER_MINUTES
            ):
                previous_start = current_bar.ts_utc
                continue
            direction = _cross(previous_ha, current_ha)
            if direction:
                event = current_bar.ts_utc + timedelta(minutes=HIGHER_MINUTES)
                index = next((i for i, bar in enumerate(chart) if bar.ts_utc >= event), -1)
                if index >= 0:
                    result.append(Signal(chart[index].ts_utc, direction, index))
            previous_start = current_bar.ts_utc
        return tuple(result)

    result = []
    previous_closed: tuple[float, float] | None = None
    previous_group_start: datetime | None = None
    for group in groups:
        contiguous = previous_group_start is None or group[
            0
        ].ts_utc - previous_group_start == timedelta(minutes=HIGHER_MINUTES)
        if not contiguous:
            previous_closed = None
        previous_eval = previous_closed
        first = group[0]
        for count in range(1, len(group) + 1):
            prefix = group[:count]
            if variant == "forming_reset":
                current = forming_heikin_ashi(prefix, None)
            else:
                current = forming_heikin_ashi(prefix, previous_closed)
            if previous_eval is not None:
                direction = _cross(previous_eval, current)
                if direction:
                    index = next(
                        (i for i, bar in enumerate(chart) if bar.ts_utc == prefix[-1].ts_utc), -1
                    )
                    if index >= 0:
                        result.append(Signal(prefix[-1].ts_utc, direction, index))
            previous_eval = current
        # A new 54m candle starts from its completed predecessor in the carried
        # model; the reset model still reports the completed HA for comparison.
        previous_closed = forming_heikin_ashi(
            group, previous_closed if variant == "forming_carried" else None
        )
        previous_group_start = first.ts_utc
    return tuple(result)


def _feature_set(chart: tuple[Candle, ...]) -> Features:
    params = TaiParams()
    atr_values = indicators.atr(chart, params.atr_period)
    ema_values = indicators.ema([bar.close for bar in chart], params.ema_period)
    adx_values = indicators.adx(chart, params.adx_period)
    plus_di, minus_di = directional_index(chart, params.adx_period)
    return Features(
        chart,
        atr_values,
        ema_values,
        adx_values,
        plus_di,
        minus_di,
        fallback_vwap(chart),
        tuple(None for _ in chart),
    )


def _signals_after_filters(
    chart: tuple[Candle, ...], candidates: Iterable[Signal]
) -> tuple[tuple[Signal, ...], int]:
    params = TaiParams()
    candidate_by_index = {item.index: item.direction for item in candidates}
    base = _feature_set(chart)
    features = Features(
        base.bars,
        base.atr,
        base.ema,
        base.adx,
        base.plus_di,
        base.minus_di,
        base.vwap,
        tuple(candidate_by_index.get(index) for index in range(len(chart))),
    )
    before_warmup = 0
    result: list[Signal] = []
    for index, direction in sorted(candidate_by_index.items()):
        if index < WARMUP_BARS:
            before_warmup += 1
            continue
        if _passes(features, index, direction, params):
            result.append(Signal(chart[index].ts_utc, direction, index))
    return tuple(result), before_warmup


def _refresh_candidates(chart: tuple[Candle, ...], gated: bool) -> tuple[Signal, ...]:
    """Generate the Pine refresh path with an optional virtual-trade gate.

    The no-gate variant treats the strategy as flat after each 12-bar refresh
    interval.  The gated variant keeps the virtual trade open until its swing
    stop or 3R target is touched, matching the stop-first ordering used by the
    Tai simulator.  Both variants use the registered filters and risk rule
    without tuning.
    """
    params = TaiParams()
    features = _feature_set(chart)
    result: list[Signal] = []
    last_signal = -params.refresh_bars
    open_trade: dict[str, float | str] | None = None
    for index, bar in enumerate(chart):
        if open_trade is not None:
            trade_direction = str(open_trade["direction"])
            stop = float(open_trade["stop"])
            target = float(open_trade["target"])
            stop_hit = bar.low <= stop if trade_direction == "long" else bar.high >= stop
            target_hit = bar.high >= target if trade_direction == "long" else bar.low <= target
            if stop_hit or target_hit:
                open_trade = None
            else:
                continue
        if index < WARMUP_BARS or index - last_signal < params.refresh_bars:
            continue
        direction: str | None = None
        for candidate_direction in ("long", "short"):
            if _passes(features, index, candidate_direction, params):
                risk_data = _risk(features, index, candidate_direction, params)
                if risk_data is not None:
                    direction = candidate_direction
                    stop, risk = risk_data
                    if gated:
                        target = (
                            bar.close + params.target_multiple * risk
                            if direction == "long"
                            else bar.close - params.target_multiple * risk
                        )
                        open_trade = {"direction": direction, "stop": stop, "target": target}
                    break
        if direction is not None:
            result.append(Signal(bar.ts_utc, direction, index))
            last_signal = index
    return tuple(result)


def match_entries(
    entries: Sequence[OwnerEntry], signals: Sequence[Signal], tolerance_bars: int
) -> dict[str, Any]:
    tolerance = timedelta(minutes=TIMEFRAME_MINUTES * tolerance_bars)
    used: set[int] = set()
    matched: dict[int, int] = {}
    for entry_index, entry in enumerate(entries):
        choices = [
            (abs(signal.time - entry.entry_time), signal_index)
            for signal_index, signal in enumerate(signals)
            if signal_index not in used
            and signal.direction == entry.direction
            and abs(signal.time - entry.entry_time) <= tolerance
        ]
        if choices:
            _, signal_index = min(choices)
            used.add(signal_index)
            matched[entry_index] = signal_index
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"entries": 0, "hits": 0})
    for index, entry in enumerate(entries):
        day = entry.entry_time.date().isoformat()
        by_day[day]["entries"] += 1
        by_day[day]["hits"] += int(index in matched)
    for signal_index, signal in enumerate(signals):
        if signal_index not in used:
            day = signal.time.date().isoformat()
            by_day[day]["extra_signals"] = by_day[day].get("extra_signals", 0) + 1
    for item in by_day.values():
        item.setdefault("extra_signals", 0)
    return {
        "hits": len(matched),
        "misses": len(entries) - len(matched),
        "extra_signals": len(signals) - len(used),
        "by_day": dict(sorted(by_day.items())),
    }


def _price_basis(entries: Sequence[OwnerEntry], minutes: Sequence[Candle]) -> dict[str, Any]:
    by_time = {item.ts_utc: item.close for item in minutes}
    differences = [
        entry.entry_price - by_time[entry.entry_time]
        for entry in entries
        if entry.entry_time in by_time
    ]
    ordered = sorted(differences)
    return {
        "matched_entries": len(differences),
        "missing_entry_minutes": len(entries) - len(differences),
        "owner_minus_nas100_mid_points": {
            "median": ordered[len(ordered) // 2] if ordered else None,
            "min": min(ordered) if ordered else None,
            "max": max(ordered) if ordered else None,
        },
    }


def _ha_states(chart: tuple[Candle, ...], anchor: str) -> dict[datetime, str]:
    """Return the forming 54m HA state at each closed 3m bar."""
    states: dict[datetime, str] = {}
    previous_closed: tuple[float, float] | None = None
    previous_start: datetime | None = None
    for group in _higher_groups(chart, anchor):
        if previous_start is not None and group[0].ts_utc - previous_start != timedelta(
            minutes=HIGHER_MINUTES
        ):
            previous_closed = None
        for count in range(1, len(group) + 1):
            open_price, close = forming_heikin_ashi(group[:count], previous_closed)
            states[group[count - 1].ts_utc] = (
                "long" if close > open_price else "short" if close < open_price else "neutral"
            )
        previous_closed = forming_heikin_ashi(group, previous_closed)
        previous_start = group[0].ts_utc
    return states


def _daily_basis(entries: Sequence[OwnerEntry], minutes: Sequence[Candle]) -> dict[str, float]:
    prices = {item.ts_utc: item.close for item in minutes}
    values: dict[str, list[float]] = defaultdict(list)
    for entry in entries:
        if entry.entry_time in prices:
            values[entry.entry_time.date().isoformat()].append(
                entry.entry_price - prices[entry.entry_time]
            )
    return {day: sorted(items)[len(items) // 2] for day, items in values.items()}


def _condition_table(
    entries: Sequence[OwnerEntry], chart: tuple[Candle, ...], minutes: Sequence[Candle]
) -> dict[str, Any]:
    """Evaluate every registered filter at each owner entry without tuning."""
    params = TaiParams()
    features = _feature_set(chart)
    by_time = {bar.ts_utc: index for index, bar in enumerate(chart)}
    ha_states = _ha_states(chart, "utc_midnight")
    basis = _daily_basis(entries, minutes)
    rows: list[dict[str, Any]] = []
    names = ("ha_state", "ema_side", "adx_di", "vwap_side", "dont_chase", "risk_atr")
    counts = {name: 0 for name in names}
    failed_counts = {name: 0 for name in names}
    raw_errors: list[float] = []
    adjusted_errors: list[float] = []
    for entry_number, entry in enumerate(entries, start=1):
        index = by_time.get(entry.entry_time)
        if index is None:
            rows.append(
                {
                    "entry": entry_number,
                    "time": entry.entry_time,
                    "direction": entry.direction,
                    "missing_bar": True,
                }
            )
            continue
        bar = chart[index]
        atr = features.atr[index]
        ema = features.ema[index]
        adx = features.adx[index]
        plus = features.plus_di[index]
        minus = features.minus_di[index]
        vwap = features.vwap[index]
        risk_data = _risk(features, index, entry.direction, params)
        ha_ok = ha_states.get(entry.entry_time) == entry.direction
        ema_ok = ema is not None and (
            bar.close > ema if entry.direction == "long" else bar.close < ema
        )
        adx_ok = (
            adx is not None
            and adx >= params.adx_min
            and plus is not None
            and minus is not None
            and (plus > minus if entry.direction == "long" else minus > plus)
        )
        vwap_ok = vwap is not None and (
            bar.close > vwap if entry.direction == "long" else bar.close < vwap
        )
        chase_ratio = abs(bar.close - vwap) / atr if atr and vwap is not None else None
        chase_ok = chase_ratio is not None and chase_ratio <= params.vwap_distance_atr
        risk_ratio = risk_data[1] / atr if risk_data and atr else None
        risk_ok = risk_ratio is not None and risk_ratio <= params.max_risk_atr
        checks = {
            "ha_state": ha_ok,
            "ema_side": ema_ok,
            "adx_di": adx_ok,
            "vwap_side": vwap_ok,
            "dont_chase": chase_ok,
            "risk_atr": risk_ok,
        }
        failed = [name for name in names if not checks[name]]
        for name in names:
            counts[name] += int(checks[name])
            failed_counts[name] += int(not checks[name])
        row: dict[str, Any] = {
            "entry": entry_number,
            "time": entry.entry_time,
            "direction": entry.direction,
            "ha_state": ha_states.get(entry.entry_time),
            "ema": ema,
            "ema_side": ema_ok,
            "adx": adx,
            "plus_di": plus,
            "minus_di": minus,
            "adx_di": adx_ok,
            "vwap": vwap,
            "vwap_side": vwap_ok,
            "distance_atr": chase_ratio,
            "dont_chase": chase_ok,
            "risk_atr": risk_ratio,
            "risk_atr_ok": risk_ok,
            "passes_all_filters": not failed,
            "failed_conditions": failed,
            "owner_stop_price": entry.stop_price,
        }
        if risk_data:
            structure_stop, structure_distance = risk_data
            day_basis = basis.get(entry.entry_time.date().isoformat(), 0.0)
            adjusted_stop = structure_stop + day_basis
            owner_distance = (
                abs(entry.entry_price - entry.stop_price) if entry.stop_price is not None else None
            )
            adjusted_distance = abs(entry.entry_price - adjusted_stop)
            row.update(
                {
                    "structure_stop_price": structure_stop,
                    "structure_risk_points": structure_distance,
                    "basis_points": day_basis,
                    "basis_adjusted_structure_stop_price": adjusted_stop,
                    "owner_stop_distance_points": owner_distance,
                    "structure_stop_error_points": (
                        abs(owner_distance - structure_distance)
                        if owner_distance is not None
                        else None
                    ),
                    "basis_adjusted_stop_error_points": (
                        abs(owner_distance - adjusted_distance)
                        if owner_distance is not None
                        else None
                    ),
                }
            )
            if owner_distance is not None:
                raw_errors.append(abs(owner_distance - structure_distance))
                adjusted_errors.append(abs(owner_distance - adjusted_distance))
        rows.append(row)

    def median(values: list[float]) -> float | None:
        ordered = sorted(values)
        return ordered[len(ordered) // 2] if ordered else None

    return {
        "anchor": "utc_midnight",
        "entries": rows,
        "condition_pass_counts": counts,
        "condition_fail_counts": failed_counts,
        "all_filters_passed": sum(1 for row in rows if row.get("passes_all_filters")),
        "stop_fit": {
            "entries_with_owner_stop": len(raw_errors),
            "median_raw_error_points": median(raw_errors),
            "median_basis_adjusted_error_points": median(adjusted_errors),
        },
    }


def _plain_report(payload: dict[str, Any]) -> str:
    lines = [
        "# T-151 owner trades comparison",
        "",
        (
            "Diagnostic only. Owner timestamps are interpreted as UTC; the sealed holdout, broker, "
            "gates, costs and ledger were not read."
        ),
        "",
        "| Variant | Anchor | Signals | Warm-up excluded | ±1 | ±2 | ±5 | Extra at ±5 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in payload["groups"]:
        m = item["matches"]
        lines.append(
            f"| {item['variant']} | {item['anchor']} | {item['signals']} | "
            f"{item['warmup_excluded']} | {m['1']['hits']}/31 | {m['2']['hits']}/31 | "
            f"{m['5']['hits']}/31 | {m['5']['extra_signals']} |"
        )
    lines += [
        "",
        f"Trial count: {payload['trial_count']} "
        f"({len(payload['variants'])} variants x three session anchors).",
        "",
        (
            "The forming-candle hypothesis is supported only when a forming variant materially "
            "increases same-direction owner-entry hits without parameter tuning; the table "
            "reports every pre-registered anchor and variant."
        ),
        "",
        f"Price basis: {payload['price_basis']}",
        "",
        "## Refresh path and per-entry conditions",
        "",
        "Variant `refresh` emits a filter-and-risk refresh every 12 flat bars "
        "without virtual in-trade gating; `refresh_gated` keeps a virtual trade "
        "open until its stop or 3R target. The condition table uses the "
        "UTC-midnight 54m anchor and evaluates all 31 owner entries without "
        "parameter tuning.",
        "",
        f"Condition pass counts: {payload['condition_table']['condition_pass_counts']}",
        f"Condition failure counts: {payload['condition_table']['condition_fail_counts']}",
        f"All filters passed: {payload['condition_table']['all_filters_passed']}/31.",
        f"Stop fit: {payload['condition_table']['stop_fit']}",
        "",
        "| Entry | Time | Direction | HA | EMA | ADX/DI | VWAP | Chase | Risk | "
        "Failed conditions |",
        "|---:|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {row['entry']} | {row['time']} | {row['direction']} | "
            f"{row.get('ha_state')} | {row.get('ema_side')} | {row.get('adx_di')} | "
            f"{row.get('vwap_side')} | {row.get('dont_chase')} | "
            f"{row.get('risk_atr_ok')} | "
            f"{', '.join(row.get('failed_conditions', [])) or 'none'} |"
            for row in payload["condition_table"]["entries"]
        ),
        "",
        "Safety: broker calls 0, orders 0, sealed holdout read false, and no gate, cost, ledger "
        "or promotion writes.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument(
        "--owner-file",
        type=Path,
        default=Path("docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv"),
    )
    parser.add_argument(
        "--output", type=Path, default=Path("docs/evidence/t151_owner_trades_summary.json")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("docs/evidence/t151_owner_trades_report.md")
    )
    args = parser.parse_args()
    entries = read_owner_entries(args.owner_file)
    if len(entries) != 31:
        raise RuntimeError(f"expected 31 owner positions, found {len(entries)}")
    start = min(entry.entry_time for entry in entries) - timedelta(days=2)
    end = max(entry.entry_time for entry in entries) + ENTRY_WINDOW
    minutes = read_nas100_minutes(args.lake, start, end)
    if not minutes:
        raise RuntimeError("NAS100 1m Capital.com data is missing")
    chart = aggregate_3m(minutes)
    groups: list[dict[str, Any]] = []
    for variant in VARIANTS:
        for anchor in ANCHORS:
            candidates = _variant_candidates(chart, anchor, variant)
            signals, warmup_excluded = _signals_after_filters(chart, candidates)
            groups.append(
                {
                    "variant": variant,
                    "anchor": anchor,
                    "candidate_signals": len(candidates),
                    "signals": len(signals),
                    "warmup_excluded": warmup_excluded,
                    "matches": {
                        str(tolerance): match_entries(entries, signals, tolerance)
                        for tolerance in (1, 2, 5)
                    },
                }
            )
    payload: dict[str, Any] = {
        "task": "T-151",
        "generated_at": datetime.now(UTC),
        "diagnostic_only": True,
        "owner_entry_count": len(entries),
        "owner_long_entries": sum(item.direction == "long" for item in entries),
        "owner_short_entries": sum(item.direction == "short" for item in entries),
        "data": {
            "lake_rows_1m": len(minutes),
            "chart_bars_3m": len(chart),
            "first_1m": minutes[0].ts_utc,
            "last_1m": minutes[-1].ts_utc,
            "bar_policy": (
                "UTC clock 3m buckets; incomplete buckets are retained but never used in a "
                "valid 54m group."
            ),
        },
        "anchors": ANCHORS,
        "variants": VARIANTS,
        "trial_count": len(VARIANTS) * len(ANCHORS),
        "groups": groups,
        "price_basis": _price_basis(entries, minutes),
        "condition_table": _condition_table(entries, chart, minutes),
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "sealed_holdout_read": False,
            "gate1_evaluated": False,
            "cost_ledger_promotion_writes": 0,
        },
    }
    _write_json(args.output, payload)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_plain_report(payload), encoding="utf-8")
    print(
        json.dumps(
            {"task": "T-151", "entries": len(entries), "groups": len(groups)}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
