#!/usr/bin/env python3
"""T-155 diagnostic: compare Tai indicators with independent references.

The command reads only recorded NAS100 candles and the owner-entry CSV.  It never
calls a broker, reads the sealed holdout, or writes gate, cost, ledger, or
promotion state.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from trader.analysis import indicators
from trader.data.resample import Candle

try:
    from scripts.lab_t151_owner_trades import (
        _condition_table,
        _daily_basis,
        _ha_states,
        _risk,
        aggregate_3m,
        read_nas100_minutes,
        read_owner_entries,
    )
    from scripts.lab_tai_smart_filter import (
        Features,
        TaiParams,
        _passes,
        directional_index,
        fallback_vwap,
    )
except ModuleNotFoundError:  # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.lab_t151_owner_trades import (
        _condition_table,
        _daily_basis,
        _ha_states,
        _risk,
        aggregate_3m,
        read_nas100_minutes,
        read_owner_entries,
    )
    from scripts.lab_tai_smart_filter import (
        Features,
        TaiParams,
        _passes,
        directional_index,
        fallback_vwap,
    )

PERIOD = 14
EMA_PERIOD = 200
CME_TZ = ZoneInfo("America/Chicago")


@dataclass(frozen=True, slots=True)
class MetricDiff:
    compared: int
    missing_actual: int
    missing_reference: int
    max_abs: float | None
    median_abs: float | None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def independent_ema(values: Sequence[float], period: int = EMA_PERIOD) -> tuple[float | None, ...]:
    """Independent SMA-seeded EMA reference."""
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(out)
    current = sum(values[:period]) / period
    out[period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        current = current + alpha * (values[index] - current)
        out[index] = current
    return tuple(out)


def independent_true_range(bars: Sequence[Candle]) -> tuple[float, ...]:
    out: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        values = [bar.high - bar.low]
        if previous_close is not None:
            values.extend((abs(bar.high - previous_close), abs(bar.low - previous_close)))
        out.append(max(values))
        previous_close = bar.close
    return tuple(out)


def independent_atr(bars: Sequence[Candle], period: int = PERIOD) -> tuple[float | None, ...]:
    ranges = independent_true_range(bars)
    out: list[float | None] = [None] * len(ranges)
    if len(ranges) < period:
        return tuple(out)
    current = sum(ranges[:period]) / period
    out[period - 1] = current
    for index in range(period, len(ranges)):
        current = (current * (period - 1) + ranges[index]) / period
        out[index] = current
    return tuple(out)


def independent_adx_di(
    bars: Sequence[Candle], period: int = PERIOD
) -> tuple[tuple[float | None, ...], tuple[float | None, ...], tuple[float | None, ...]]:
    """Independent Wilder ADX, +DI and -DI reference."""
    n = len(bars)
    adx: list[float | None] = [None] * n
    plus_di: list[float | None] = [None] * n
    minus_di: list[float | None] = [None] * n
    if n < period:
        return tuple(adx), tuple(plus_di), tuple(minus_di)
    ranges = independent_true_range(bars)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for previous, current in zip(bars[:-1], bars[1:], strict=True):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    sm_range = sum(ranges[:period])
    sm_plus = sum(plus_dm[:period])
    sm_minus = sum(minus_dm[:period])
    dx: list[float | None] = [None] * n
    for index in range(period - 1, n):
        if index >= period:
            sm_range += ranges[index] - sm_range / period
            sm_plus += plus_dm[index] - sm_plus / period
            sm_minus += minus_dm[index] - sm_minus / period
        if sm_range <= 0:
            dx[index] = 0.0
            continue
        plus = 100.0 * sm_plus / sm_range
        minus = 100.0 * sm_minus / sm_range
        plus_di[index] = plus
        minus_di[index] = minus
        total = plus + minus
        dx[index] = 0.0 if total <= 0 else 100.0 * abs(plus - minus) / total
    first = 2 * period - 2
    initial = [value for value in dx[period - 1 : first + 1] if value is not None]
    if len(initial) == period:
        current_adx = sum(initial) / period
        adx[first] = current_adx
        for index in range(first + 1, n):
            if dx[index] is not None:
                current_adx = (current_adx * (period - 1) + dx[index]) / period
                adx[index] = current_adx
    return tuple(adx), tuple(plus_di), tuple(minus_di)


def independent_heikin_ashi(bars: Sequence[Candle]) -> tuple[tuple[float, float], ...]:
    out: list[tuple[float, float]] = []
    for index, bar in enumerate(bars):
        close = (bar.open + bar.high + bar.low + bar.close) / 4.0
        open_price = (bar.open + bar.close) / 2.0 if index == 0 else (out[-1][0] + out[-1][1]) / 2.0
        out.append((open_price, close))
    return tuple(out)


def compare(actual: Sequence[float | None], reference: Sequence[float | None]) -> MetricDiff:
    missing_actual = sum(
        a is None and r is not None for a, r in zip(actual, reference, strict=True)
    )
    missing_reference = sum(
        a is not None and r is None for a, r in zip(actual, reference, strict=True)
    )
    values = [
        abs(float(a) - float(r))
        for a, r in zip(actual, reference, strict=True)
        if a is not None and r is not None
    ]
    return MetricDiff(
        len(values),
        missing_actual,
        missing_reference,
        max(values) if values else None,
        _median(values),
    )


def compare_ha(
    actual: Sequence[Candle], reference: Sequence[tuple[float, float]]
) -> dict[str, MetricDiff]:
    opens = compare([bar.open for bar in actual], [item[0] for item in reference])
    closes = compare([bar.close for bar in actual], [item[1] for item in reference])
    return {"open": opens, "close": closes}


def _session_start(stamp: datetime) -> datetime:
    local = stamp.astimezone(CME_TZ)
    start = datetime(local.year, local.month, local.day, 17, tzinfo=CME_TZ)
    if stamp < start.astimezone(UTC):
        start -= timedelta(days=1)
    return start.astimezone(UTC)


def session_vwap(bars: Sequence[Candle], weighting: str, anchor: str) -> tuple[float | None, ...]:
    result: list[float | None] = []
    current: datetime | object = object()
    weighted = 0.0
    weight = 0.0
    for bar in bars:
        key = bar.ts_utc.date() if anchor == "utc_midnight" else _session_start(bar.ts_utc)
        if key != current:
            current = key
            weighted = 0.0
            weight = 0.0
        typical = (bar.high + bar.low + bar.close) / 3.0
        bar_weight = bar.volume if weighting == "tick_volume" and bar.volume > 0 else 1.0
        weighted += typical * bar_weight
        weight += bar_weight
        result.append(weighted / weight if weight else None)
    return tuple(result)


def sensitivity(
    entries: Sequence[object],
    chart: tuple[Candle, ...],
    vwap: tuple[float | None, ...],
) -> dict[str, object]:
    params = TaiParams()
    atr_values = indicators.atr(chart, params.atr_period)
    ema_values = indicators.ema([bar.close for bar in chart], params.ema_period)
    adx_values = indicators.adx(chart, params.adx_period)
    plus_di, minus_di = directional_index(chart, params.adx_period)
    features = Features(
        chart,
        atr_values,
        ema_values,
        adx_values,
        plus_di,
        minus_di,
        vwap,
        tuple(None for _ in chart),
    )
    by_time = {bar.ts_utc: index for index, bar in enumerate(chart)}
    ha_states = _ha_states(chart, "utc_midnight")
    passed = 0
    names = ("ha_state", "ema_side", "adx_di", "vwap_side", "dont_chase", "risk_atr")
    fail_counts = {name: 0 for name in names}
    for entry in entries:
        index = by_time.get(entry.entry_time)
        if index is None:
            continue
        bar = chart[index]
        atr = features.atr[index]
        ema = features.ema[index]
        adx = features.adx[index]
        plus = features.plus_di[index]
        minus = features.minus_di[index]
        value = features.vwap[index]
        risk = _risk(features, index, entry.direction, params)
        checks = {
            "ha_state": ha_states.get(entry.entry_time) == entry.direction,
            "ema_side": ema is not None
            and (bar.close > ema if entry.direction == "long" else bar.close < ema),
            "adx_di": (
                adx is not None
                and adx >= params.adx_min
                and plus is not None
                and minus is not None
                and (plus > minus if entry.direction == "long" else minus > plus)
            ),
            "vwap_side": value is not None
            and (bar.close > value if entry.direction == "long" else bar.close < value),
            "dont_chase": (
                value is not None
                and atr is not None
                and atr > 0
                and abs(bar.close - value) / atr <= params.vwap_distance_atr
            ),
            "risk_atr": (
                risk is not None
                and atr is not None
                and atr > 0
                and risk[1] / atr <= params.max_risk_atr
            ),
        }
        for name, ok in checks.items():
            fail_counts[name] += int(not ok)
        passed += int(all(checks.values()))
    return {
        "entries_evaluated": len(entries),
        "all_filters_passed": passed,
        "condition_fail_counts": fail_counts,
    }


def _json_metric(value: MetricDiff | object) -> dict[str, object]:
    if isinstance(value, MetricDiff):
        return {
            "compared": value.compared,
            "missing_actual": value.missing_actual,
            "missing_reference": value.missing_reference,
            "max_abs": value.max_abs,
            "median_abs": value.median_abs,
        }
    return value  # type: ignore[return-value]


def run(lake: Path, owner_file: Path) -> dict[str, object]:
    entries = read_owner_entries(owner_file)
    if len(entries) != 31:
        raise RuntimeError(f"expected 31 owner entries, found {len(entries)}")
    start = min(item.entry_time for item in entries) - timedelta(days=2)
    end = max(item.entry_time for item in entries) + timedelta(minutes=5)
    minutes = read_nas100_minutes(lake, start, end)
    if not minutes:
        raise RuntimeError("NAS100 1m Capital.com data is missing")
    chart = aggregate_3m(minutes)
    closes = [bar.close for bar in chart]
    actual_adx = indicators.adx(chart, PERIOD)
    actual_plus, actual_minus = directional_index(chart, PERIOD)
    ref_adx, ref_plus, ref_minus = independent_adx_di(chart, PERIOD)
    actual_ha = [(bar.open, bar.close) for bar in indicators_heikin(chart)]
    ref_ha = independent_heikin_ashi(chart)
    references = {
        "ema200": _json_metric(
            compare(
                indicators.ema(closes, EMA_PERIOD),
                independent_ema(closes, EMA_PERIOD),
            )
        ),
        "atr14": _json_metric(
            compare(indicators.atr(chart, PERIOD), independent_atr(chart, PERIOD))
        ),
        "adx14": _json_metric(compare(actual_adx, ref_adx)),
        "plus_di14": _json_metric(compare(actual_plus, ref_plus)),
        "minus_di14": _json_metric(compare(actual_minus, ref_minus)),
        "heikin_ashi": {
            name: _json_metric(metric)
            for name, metric in compare_ha(indicators_heikin(chart), ref_ha).items()
        },
    }
    sensitivities = {
        "utc_equal": sensitivity(entries, chart, session_vwap(chart, "equal", "utc_midnight")),
        "cme_equal": sensitivity(entries, chart, session_vwap(chart, "equal", "cme_open")),
        "cme_tick_volume": sensitivity(
            entries, chart, session_vwap(chart, "tick_volume", "cme_open")
        ),
    }
    optional = {}
    for package in ("talib", "pandas_ta"):
        try:
            __import__(package)
            optional[package] = "available"
        except ImportError:
            optional[package] = "unavailable"
    return {
        "task": "T-155",
        "generated_at": datetime.now(UTC).isoformat(),
        "diagnostic_only": True,
        "data": {
            "minute_rows": len(minutes),
            "chart_bars_3m": len(chart),
            "first_3m": chart[0].ts_utc.isoformat(),
            "last_3m": chart[-1].ts_utc.isoformat(),
        },
        "independent_references": references,
        "optional_libraries": optional,
        "vwap_sensitivity": sensitivities,
        "unverifiable": [
            "TradingView VWAP session anchor and real NQ1! volume",
            "54m session anchoring",
            "futures-vs-CFD data",
        ],
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "sealed_holdout_read": False,
            "gate1_evaluated": False,
            "cost_ledger_promotion_writes": 0,
            "lake_writes": 0,
        },
    }


def indicators_heikin(chart: Sequence[Candle]) -> tuple[Candle, ...]:
    out: list[Candle] = []
    for index, bar in enumerate(chart):
        close = (bar.open + bar.high + bar.low + bar.close) / 4.0
        open_price = (
            (bar.open + bar.close) / 2.0
            if index == 0
            else (out[-1].open + out[-1].close) / 2.0
        )
        out.append(
            Candle(
                bar.ts_utc,
                open_price,
                max(bar.high, open_price, close),
                min(bar.low, open_price, close),
                close,
                bar.volume,
                bar.series_key,
                bar.complete,
                bar.n_minutes,
            )
        )
    return tuple(out)


def report(payload: dict[str, object]) -> str:
    lines = [
        "# T-155 Tai indicator parity",
        "",
        (
            "Diagnostic-only; recorded NAS100 data only. No broker, holdout, gate, "
            "cost, ledger, promotion, or lake writes."
        ),
        "",
        "## Independent reference comparisons",
        "",
        (
            "| Indicator | Compared | Max absolute difference | Median absolute "
            "difference | Missing actual | Missing reference |"
        ),
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, metric in payload["independent_references"].items():
        if name == "heikin_ashi":
            for field, item in metric.items():
                lines.append(
                    f"| Heikin Ashi {field} | {item['compared']} | "
                    f"{item['max_abs']} | {item['median_abs']} | "
                    f"{item['missing_actual']} | {item['missing_reference']} |"
                )
        else:
            lines.append(
                f"| {name} | {metric['compared']} | {metric['max_abs']} | "
                f"{metric['median_abs']} | {metric['missing_actual']} | "
                f"{metric['missing_reference']} |"
            )
    lines += [
        "",
        "TA-Lib/pandas-ta availability: "
        + json.dumps(payload["optional_libraries"], sort_keys=True)
        + ".",
        "",
        "## VWAP sensitivity",
        "",
        "| VWAP mode | Entries evaluated | All filters passed | Failing conditions |",
        "|---|---:|---:|---|",
    ]
    for name, item in payload["vwap_sensitivity"].items():
        lines.append(
            f"| {name} | {item['entries_evaluated']} | "
            f"{item['all_filters_passed']} | {item['condition_fail_counts']} |"
        )
    lines += ["", "## Remaining unverifiable items", ""]
    lines.extend(f"- {item}" for item in payload["unverifiable"])
    lines += ["", "## Safety", "", json.dumps(payload["safety"], sort_keys=True)]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument(
        "--owner-file",
        type=Path,
        default=Path(
            "docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv"
        ),
    )
    parser.add_argument(
        "--output", type=Path, default=Path("docs/evidence/t155_tai_parity_summary.json")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("docs/evidence/t155_tai_parity_report.md")
    )
    args = parser.parse_args()
    payload = run(args.lake, args.owner_file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "task": "T-155",
                "entries": payload["vwap_sensitivity"]["utc_equal"]["entries_evaluated"],
                "chart_bars": payload["data"]["chart_bars_3m"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
