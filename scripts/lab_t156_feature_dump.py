#!/usr/bin/env python3
"""Dump raw Tai indicator values around every owner entry.

This is a read-only T-156 diagnostic.  It reads the recorded NAS100 lake and
the owner CSV only.  It never calls a broker, reads the sealed holdout, or
writes gate, cost, ledger, or promotion state.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trader.analysis import indicators
from trader.data.resample import Candle

try:
    from scripts.lab_t151_owner_trades import (
        HIGHER_BARS,
        HIGHER_MINUTES,
        TIMEFRAME_MINUTES,
        OwnerEntry,
        _higher_groups,
        _risk,
        aggregate_3m,
        forming_heikin_ashi,
        read_nas100_minutes,
        read_owner_entries,
    )
    from scripts.lab_tai_smart_filter import (
        Features,
        TaiParams,
        directional_index,
        fallback_vwap,
    )
except ModuleNotFoundError:  # direct ``python scripts/lab_t156_feature_dump.py``
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.lab_t151_owner_trades import (
        HIGHER_BARS,
        HIGHER_MINUTES,
        TIMEFRAME_MINUTES,
        OwnerEntry,
        _higher_groups,
        _risk,
        aggregate_3m,
        forming_heikin_ashi,
        read_nas100_minutes,
        read_owner_entries,
    )
    from scripts.lab_tai_smart_filter import (
        Features,
        TaiParams,
        directional_index,
        fallback_vwap,
    )


def _json(value: Any) -> str:
    return json.dumps(value, default=lambda item: item.isoformat() if isinstance(item, datetime) else item, separators=(",", ":"))


def _finite(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else float(value)


def _features(chart: tuple[Candle, ...]) -> Features:
    params = TaiParams()
    atr = indicators.atr(chart, params.atr_period)
    ema = indicators.ema([bar.close for bar in chart], params.ema_period)
    adx = indicators.adx(chart, params.adx_period)
    plus, minus = directional_index(chart, params.adx_period)
    return Features(chart, atr, ema, adx, plus, minus, fallback_vwap(chart), tuple(None for _ in chart))


def _ha_values(chart: tuple[Candle, ...]) -> dict[datetime, dict[str, float | None]]:
    """Return the last closed 54m HA and the current forming HA at each 3m bar."""
    values: dict[datetime, dict[str, float | None]] = {}
    previous_closed: tuple[float, float] | None = None
    previous_start: datetime | None = None
    for group in _higher_groups(chart, "utc_midnight"):
        if previous_start is not None and group[0].ts_utc - previous_start != timedelta(minutes=HIGHER_MINUTES):
            previous_closed = None
        for count, bar in enumerate(group, start=1):
            forming = forming_heikin_ashi(group[:count], previous_closed)
            values[bar.ts_utc] = {
                "ha_closed_open": previous_closed[0] if previous_closed else None,
                "ha_closed_close": previous_closed[1] if previous_closed else None,
                "ha_forming_open": forming[0],
                "ha_forming_close": forming[1],
            }
        previous_closed = forming_heikin_ashi(group, previous_closed)
        previous_start = group[0].ts_utc
    return values


def _side_chart(chart_minutes: Sequence[Candle]) -> tuple[Candle, ...]:
    return aggregate_3m(chart_minutes)


def _read_side_minutes(lake: Path, start: datetime, end: datetime, side: str) -> tuple[Candle, ...]:
    """Read one recorded side, or the paired mid, without changing the lake."""
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pyarrow is required for T-156") from exc
    paired: dict[datetime, dict[str, Candle]] = defaultdict(dict)
    singles: dict[datetime, Candle] = {}
    for path in sorted(lake.rglob("*.parquet")):
        try:
            schema = set(pq.ParquetFile(path).schema.names)
            required = {"ts_utc", "open", "high", "low", "close"}
            if not required.issubset(schema):
                continue
            columns = sorted(required | ({"side"} if "side" in schema else set()))
            for row in pq.read_table(path, columns=columns).to_pylist():
                stamp = row["ts_utc"]
                if not isinstance(stamp, datetime):
                    stamp = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                stamp = stamp.astimezone(UTC)
                if not start <= stamp < end:
                    continue
                item = Candle(stamp, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), 0.0, ("NAS100", side), True, 1)
                row_side = str(row.get("side", "")).lower()
                if side == "mid" and row_side in {"bid", "ask"}:
                    paired[stamp][row_side] = item
                elif side == row_side or not row_side:
                    singles[stamp] = item
        except (OSError, KeyError, TypeError, ValueError, OverflowError):
            continue
    if side == "mid":
        result = []
        for stamp, sides in paired.items():
            if "bid" in sides and "ask" in sides:
                bid, ask = sides["bid"], sides["ask"]
                result.append(replace(bid, open=(bid.open + ask.open) / 2, high=(bid.high + ask.high) / 2, low=(bid.low + ask.low) / 2, close=(bid.close + ask.close) / 2, series_key=("NAS100", "mid")))
        result.extend(singles.values())
        return tuple(sorted({bar.ts_utc: bar for bar in result}.values(), key=lambda bar: bar.ts_utc))
    return tuple(sorted(singles.values(), key=lambda bar: bar.ts_utc))


def _condition(features: Features, index: int, direction: str, params: TaiParams) -> dict[str, Any]:
    bar = features.bars[index]
    atr = _finite(features.atr[index])
    ema = _finite(features.ema[index])
    adx = _finite(features.adx[index])
    plus = _finite(features.plus_di[index])
    minus = _finite(features.minus_di[index])
    vwap = _finite(features.vwap[index])
    distance = abs(bar.close - vwap) / atr if atr and vwap is not None else None
    risk_data = _risk(features, index, direction, params)
    risk_atr = risk_data[1] / atr if risk_data and atr else None
    ema_margin = (bar.close - ema) if direction == "long" and ema is not None else (ema - bar.close) if direction == "short" and ema is not None else None
    di_margin = (plus - minus) if direction == "long" and plus is not None and minus is not None else (minus - plus) if direction == "short" and plus is not None and minus is not None else None
    vwap_margin = (bar.close - vwap) if direction == "long" and vwap is not None else (vwap - bar.close) if direction == "short" and vwap is not None else None
    return {
        "ema_pass": ema_margin is not None and ema_margin > 0,
        "adx_pass": adx is not None and adx >= params.adx_min,
        "di_pass": di_margin is not None and di_margin > 0,
        "vwap_side_pass": vwap_margin is not None and vwap_margin > 0,
        "dont_chase_pass": distance is not None and distance <= params.vwap_distance_atr,
        "risk_pass": risk_atr is not None and risk_atr <= params.max_risk_atr,
        "ema_margin_points": ema_margin,
        "di_margin_points": di_margin,
        "vwap_side_margin_points": vwap_margin,
        "adx_shortfall_points": max(0.0, params.adx_min - adx) if adx is not None else None,
        "di_shortfall_points": max(0.0, -di_margin) if di_margin is not None else None,
        "vwap_side_shortfall_points": max(0.0, -vwap_margin) if vwap_margin is not None else None,
        "distance_vwap_atr": distance,
        "dont_chase_excess_atr": max(0.0, distance - params.vwap_distance_atr) if distance is not None else None,
        "risk_atr": risk_atr,
        "risk_excess_atr": max(0.0, risk_atr - params.max_risk_atr) if risk_atr is not None else None,
        "structure_stop": risk_data[0] if risk_data else None,
        "structure_risk_points": risk_data[1] if risk_data else None,
    }


def _entry_rows(entries: Sequence[OwnerEntry], chart: tuple[Candle, ...], minutes: Sequence[Candle]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = TaiParams()
    features = _features(chart)
    by_time = {bar.ts_utc: i for i, bar in enumerate(chart)}
    minute_by_time = {bar.ts_utc: bar for bar in minutes}
    ha = _ha_values(chart)
    rows: list[dict[str, Any]] = []
    shortfalls: dict[str, list[float]] = defaultdict(list)
    signs: dict[str, list[float]] = defaultdict(list)
    coverage = {"missing_minutes": 0, "zero_range_minutes": 0, "flat_filled_minutes": 0, "bars_fewer_than_3_real_minutes": 0}
    for entry_number, entry in enumerate(entries, start=1):
        center = by_time.get(entry.entry_time)
        window: list[dict[str, Any]] = []
        if center is None:
            rows.append({"entry_number": entry_number, "entry_time": entry.entry_time.isoformat(), "direction": entry.direction, "missing_entry_bar": True, "window_json": "[]"})
            continue
        for offset in range(-3, 4):
            index = center + offset
            if index < 0 or index >= len(chart):
                continue
            bar = chart[index]
            cond = _condition(features, index, entry.direction, params)
            item = {"offset_bars": offset, "chart_time": bar.ts_utc.isoformat(), "close": bar.close, "ema200": features.ema[index], "adx14": features.adx[index], "plus_di": features.plus_di[index], "minus_di": features.minus_di[index], "vwap": features.vwap[index], "atr14": features.atr[index], "distance_vwap_atr": cond["distance_vwap_atr"], "chart_complete": bar.complete, "source_minute_count": bar.n_minutes, "ha_closed_open": ha.get(bar.ts_utc, {}).get("ha_closed_open"), "ha_closed_close": ha.get(bar.ts_utc, {}).get("ha_closed_close"), "ha_forming_open": ha.get(bar.ts_utc, {}).get("ha_forming_open"), "ha_forming_close": ha.get(bar.ts_utc, {}).get("ha_forming_close"), "structure_stop": cond["structure_stop"], "structure_risk_points": cond["structure_risk_points"], "risk_atr": cond["risk_atr"], **cond}
            window.append(item)
            if offset == 0:
                for name in ("adx_shortfall_points", "di_shortfall_points", "vwap_side_shortfall_points", "dont_chase_excess_atr", "risk_excess_atr"):
                    if item[name] is not None:
                        shortfalls[name].append(float(item[name]))
                for name in ("ema_margin_points", "di_margin_points", "vwap_side_margin_points"):
                    if item.get(name) is not None:
                        signs[name].append(float(item[name]))
        start = entry.entry_time - timedelta(minutes=9)
        expected = [start + timedelta(minutes=i) for i in range(19)]
        observed = [minute_by_time.get(stamp) for stamp in expected]
        coverage["missing_minutes"] += sum(item is None for item in observed)
        coverage["zero_range_minutes"] += sum(item is not None and item.high == item.low for item in observed)
        coverage["flat_filled_minutes"] += sum(item is not None and item.open == item.high == item.low == item.close for item in observed)
        coverage["bars_fewer_than_3_real_minutes"] += sum(int(item.get("source_minute_count", 0) < 3) for item in window)
        summary = window[3] if len(window) >= 4 else (window[0] if window else {})
        rows.append({"entry_number": entry_number, "entry_time": entry.entry_time.isoformat(), "direction": entry.direction, "entry_price": entry.entry_price, "owner_stop_price": entry.stop_price, "missing_entry_bar": False, **summary, "window_json": _json(window)})
    return rows, {"coverage_totals": coverage, "shortfalls": shortfalls, "signed_margins": signs}


def _sensitivity(entries: Sequence[OwnerEntry], charts: dict[str, tuple[Candle, ...]]) -> dict[str, Any]:
    params = TaiParams()
    result: dict[str, Any] = {}
    for side, chart in charts.items():
        features = _features(chart)
        by_time = {bar.ts_utc: i for i, bar in enumerate(chart)}
        adx_pass = vwap_pass = 0
        available = 0
        for entry in entries:
            index = by_time.get(entry.entry_time)
            if index is None:
                continue
            available += 1
            condition = _condition(features, index, entry.direction, params)
            adx_pass += int(condition["adx_pass"] and condition["di_pass"])
            vwap_pass += int(condition["vwap_side_pass"])
        result[side] = {"available_entries": available, "adx_di_pass": adx_pass, "vwap_side_pass": vwap_pass}
    return result


def _median(values: Sequence[float]) -> float | None:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def _summary(payload: dict[str, Any]) -> str:
    coverage = payload["coverage"]
    lines = ["# T-156 raw entry feature dump", "", "Diagnostic only. Values come from recorded NAS100 1m Capital.com data and the owner CSV; no broker, holdout, gate, cost, ledger, or promotion state was read or written.", "", f"Owner entries: {payload['entry_count']}; rows: {payload['row_count']} (one row per entry, with offsets -3..+3 in `window_json`).", "", "## Coverage around entries", "", f"Missing 1m bars in each entry's ±9m window (total): {coverage['missing_minutes']}", f"Zero-range 1m bars: {coverage['zero_range_minutes']}", f"Flat-filled 1m bars: {coverage['flat_filled_minutes']}", f"3m bars built from fewer than 3 recorded minutes across windows: {coverage['bars_fewer_than_3_real_minutes']}", "", "## Condition shortfalls at the entry bar", "", "| Measure | Median | Positive samples | Samples |", "|---|---:|---:|---:|"]
    for name, values in payload["shortfall_summary"].items():
        lines.append(f"| {name} | {values['median']} | {values['positive_samples']} | {values['samples']} |")
    lines += ["", "Signed margin sign consistency (positive means the condition's directional side passed):"]
    for name, values in payload["signed_margin_summary"].items():
        lines.append(f"- `{name}`: {values['positive']} positive, {values['negative']} negative, {values['zero']} zero of {values['samples']}; dominant sign `{values['dominant_sign']}`.")
    lines += ["", "## Bid/ask/mid sensitivity", "", "| Price basis | Entries with data | ADX + DI pass | VWAP side pass |", "|---|---:|---:|---:|"]
    for side, item in payload["sensitivity"].items():
        lines.append(f"| {side} | {item['available_entries']} | {item['adx_di_pass']} | {item['vwap_side_pass']} |")
    lines += ["", "## What remains unexplained", "", "The dump identifies raw values, coverage gaps, and price-basis sensitivity. It does not establish why TradingView's indicator labels differ: the recorded lake does not contain the TradingView script's internal state, alert timing, session settings, or any unrecorded bid/ask feed. No parameter tuning was performed.", "", "Safety: broker calls 0, orders 0, sealed holdout read false, and no gate, cost, ledger, or promotion writes.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--owner-file", type=Path, default=Path("docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv"))
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/t156_entry_feature_dump.csv"))
    parser.add_argument("--report", type=Path, default=Path("docs/evidence/t156_report.md"))
    args = parser.parse_args()
    entries = read_owner_entries(args.owner_file)
    if len(entries) != 31:
        raise RuntimeError(f"expected 31 owner positions, found {len(entries)}")
    start = min(entry.entry_time for entry in entries) - timedelta(days=2)
    end = max(entry.entry_time for entry in entries) + timedelta(minutes=15)
    mid_minutes = read_nas100_minutes(args.lake, start, end)
    if not mid_minutes:
        raise RuntimeError("NAS100 1m Capital.com data is missing")
    chart = aggregate_3m(mid_minutes)
    rows, diagnostic = _entry_rows(entries, chart, mid_minutes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        fieldnames: list[str] = []
        for row in rows:
            for name in row:
                if name not in fieldnames:
                    fieldnames.append(name)
        writer = csv.DictWriter(handle, fieldnames=tuple(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    shortfall_summary = {}
    for name, values in diagnostic["shortfalls"].items():
        shortfall_summary[name] = {"median": _median(values), "positive_samples": sum(item > 0 for item in values), "samples": len(values)}
    signed_margin_summary = {}
    for name, values in diagnostic["signed_margins"].items():
        positive = sum(item > 0 for item in values)
        negative = sum(item < 0 for item in values)
        zero = sum(item == 0 for item in values)
        dominant_sign = "positive" if positive > negative else "negative" if negative > positive else "mixed"
        signed_margin_summary[name] = {"positive": positive, "negative": negative, "zero": zero, "samples": len(values), "dominant_sign": dominant_sign}
    charts = {"mid": chart}
    for side in ("bid", "ask"):
        side_minutes = _read_side_minutes(args.lake, start, end, side)
        if side_minutes:
            charts[side] = _side_chart(side_minutes)
    payload = {"task": "T-156", "generated_at": datetime.now(UTC).isoformat(), "diagnostic_only": True, "entry_count": len(entries), "row_count": len(rows), "coverage": diagnostic["coverage_totals"], "shortfall_summary": shortfall_summary, "signed_margin_summary": signed_margin_summary, "sensitivity": _sensitivity(entries, charts)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_summary(payload), encoding="utf-8")
    print(json.dumps({"task": "T-156", "entries": len(entries), "rows": len(rows), "sensitivity": payload["sensitivity"], "coverage": payload["coverage"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
