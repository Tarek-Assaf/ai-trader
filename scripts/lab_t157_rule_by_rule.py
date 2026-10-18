#!/usr/bin/env python3
"""Locate the first Tai rule that disagrees with each owner entry.

This is a diagnostic-only replay.  It reads the owner CSV and recorded NAS100
training-lake bars, and writes a rule-by-rule table.  It never calls a broker,
reads the sealed holdout, changes strategy parameters, or writes production
state.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trader.analysis import indicators
from trader.data.resample import Candle

try:
    from scripts.lab_t151_owner_trades import (
        OwnerEntry,
        _anchor_start,
        _higher_groups,
        aggregate_3m,
        read_nas100_minutes,
        read_owner_entries,
    )
    from scripts.lab_t156_feature_dump import _read_side_minutes
    from scripts.lab_tai_smart_filter import (
        Features,
        TaiParams,
        _risk,
        directional_index,
    )
except ModuleNotFoundError:  # direct ``python scripts/lab_t157_rule_by_rule.py``
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.lab_t151_owner_trades import (
        OwnerEntry,
        _anchor_start,
        _higher_groups,
        aggregate_3m,
        read_nas100_minutes,
        read_owner_entries,
    )
    from scripts.lab_t156_feature_dump import _read_side_minutes
    from scripts.lab_tai_smart_filter import (
        Features,
        TaiParams,
        _risk,
        directional_index,
    )


RULES = ("ha_state", "ema_side", "adx_di", "vwap_side", "dont_chase", "risk_atr")
OFFSETS = (-3, -2, -1, 0, 1, 2, 3)
VARIANTS = (
    "closed_wilder_sma_ema_utc",
    "forming_wilder_sma_ema_utc",
    "closed_wilder_sma_ema_riyadh",
    "closed_wilder_sma_ema_cme",
    "forming_wilder_sma_ema_cme",
    "closed_simple_sma_ema_utc",
    "closed_wilder_firstclose_ema_utc",
    "closed_wilder_sma_ema_utc_shift_minus1",
    "closed_wilder_sma_ema_utc_shift_plus1",
)


@dataclass(frozen=True, slots=True)
class RuleContext:
    features: Features
    ha_states: dict[datetime, str | None]
    anchor: str
    ha_mode: str
    adx_mode: str
    ema_mode: str


def _finite(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else float(value)


def _first_close_ema(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    if not values:
        return ()
    alpha = 2.0 / (period + 1)
    result: list[float | None] = [float(values[0])]
    current = float(values[0])
    for value in values[1:]:
        current += alpha * (float(value) - current)
        result.append(current)
    return tuple(result)


def _simple_di_adx(
    bars: Sequence[Candle], period: int
) -> tuple[tuple[float | None, ...], tuple[float | None, ...], tuple[float | None, ...]]:
    """Simple-window DI and ADX, used only as a diagnostic switch."""
    ranges = indicators.true_range(bars)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for previous, current in zip(bars[:-1], bars[1:], strict=True):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    plus: list[float | None] = [None] * len(bars)
    minus: list[float | None] = [None] * len(bars)
    dx: list[float | None] = [None] * len(bars)
    for index in range(period - 1, len(bars)):
        total_range = math.fsum(ranges[index - period + 1 : index + 1])
        if total_range <= 0:
            plus[index] = minus[index] = dx[index] = 0.0
            continue
        p = 100.0 * math.fsum(plus_dm[index - period + 1 : index + 1]) / total_range
        m = 100.0 * math.fsum(minus_dm[index - period + 1 : index + 1]) / total_range
        plus[index], minus[index] = p, m
        denominator = p + m
        dx[index] = 0.0 if denominator <= 0 else 100.0 * abs(p - m) / denominator
    adx: list[float | None] = [None] * len(bars)
    for index in range(2 * period - 2, len(bars)):
        window = dx[index - period + 1 : index + 1]
        if all(value is not None for value in window):
            adx[index] = math.fsum(value for value in window if value is not None) / period
    return tuple(plus), tuple(minus), tuple(adx)


def _session_vwap(bars: Sequence[Candle], anchor: str) -> tuple[float | None, ...]:
    result: list[float | None] = []
    session: datetime | None = None
    weighted = 0.0
    volume = 0.0
    count = 0
    for bar in bars:
        current = _anchor_start(bar.ts_utc, anchor)
        if current != session:
            session = current
            weighted = volume = 0.0
            count = 0
        typical = (bar.high + bar.low + bar.close) / 3.0
        if bar.volume > 0:
            weighted += typical * bar.volume
            volume += bar.volume
        else:
            weighted += typical
            count += 1
        result.append(weighted / volume if volume > 0 else weighted / count)
    return tuple(result)


def _ha_states(chart: Sequence[Candle], anchor: str, mode: str) -> dict[datetime, str | None]:
    result: dict[datetime, str | None] = {}
    previous: tuple[float, float] | None = None
    previous_start: datetime | None = None
    for group in _higher_groups(chart, anchor):
        if previous_start is not None and group[0].ts_utc - previous_start != timedelta(minutes=54):
            previous = None
        for count, bar in enumerate(group, start=1):
            close = (
                group[0].open
                + max(item.high for item in group[:count])
                + min(item.low for item in group[:count])
                + group[count - 1].close
            ) / 4.0
            opening = (
                (group[0].open + group[0].close) / 2.0
                if previous is None
                else (previous[0] + previous[1]) / 2.0
            )
            opening_close = (opening, close)
            selected = opening_close if mode == "forming" else previous
            result[bar.ts_utc] = (
                "long"
                if selected and selected[1] > selected[0]
                else "short"
                if selected and selected[1] < selected[0]
                else "neutral"
            )
        previous = opening_close
        previous_start = group[0].ts_utc
    return result


def _context(
    chart: tuple[Candle, ...], *, anchor: str, ha_mode: str, adx_mode: str, ema_mode: str
) -> RuleContext:
    params = TaiParams()
    atr = indicators.atr(chart, params.atr_period)
    if ema_mode == "firstclose":
        ema = _first_close_ema([bar.close for bar in chart], params.ema_period)
    else:
        ema = indicators.ema([bar.close for bar in chart], params.ema_period)
    if adx_mode == "simple":
        plus, minus, adx = _simple_di_adx(chart, params.adx_period)
    else:
        adx = indicators.adx(chart, params.adx_period)
        plus, minus = directional_index(chart, params.adx_period)
    vwap = _session_vwap(chart, anchor)
    features = Features(chart, atr, ema, adx, plus, minus, vwap, tuple(None for _ in chart))
    return RuleContext(
        features, _ha_states(chart, anchor, ha_mode), anchor, ha_mode, adx_mode, ema_mode
    )


def _variant_parts(variant: str) -> tuple[str, str, str, str, int]:
    shift = -1 if variant.endswith("shift_minus1") else 1 if variant.endswith("shift_plus1") else 0
    base = variant.removesuffix("_shift_minus1").removesuffix("_shift_plus1")
    parts = base.split("_")
    ha_mode = "forming" if parts[0] == "forming" else "closed"
    adx_mode = "simple" if "simple" in parts else "wilder"
    ema_mode = "firstclose" if "firstclose" in parts else "sma"
    anchor = (
        "cme_open" if "cme" in parts else "riyadh_midnight" if "riyadh" in parts else "utc_midnight"
    )
    return anchor, ha_mode, adx_mode, ema_mode, shift


def _evaluate(context: RuleContext, index: int, direction: str) -> dict[str, Any]:
    params = TaiParams()
    features, bar = context.features, context.features.bars[index]
    atr = _finite(features.atr[index])
    ema = _finite(features.ema[index])
    adx = _finite(features.adx[index])
    plus = _finite(features.plus_di[index])
    minus = _finite(features.minus_di[index])
    vwap = _finite(features.vwap[index])
    ha_state = context.ha_states.get(bar.ts_utc)
    risk_data = _risk(features, index, direction, params)
    distance = abs(bar.close - vwap) / atr if atr and vwap is not None else None
    risk_atr = risk_data[1] / atr if risk_data and atr else None
    checks = {
        "ha_state": ha_state == direction,
        "ema_side": ema is not None
        and (bar.close > ema if direction == "long" else bar.close < ema),
        "adx_di": (
            adx is not None
            and adx >= params.adx_min
            and plus is not None
            and minus is not None
            and (plus > minus if direction == "long" else minus > plus)
        ),
        "vwap_side": vwap is not None
        and (bar.close > vwap if direction == "long" else bar.close < vwap),
        "dont_chase": distance is not None and distance <= params.vwap_distance_atr,
        "risk_atr": risk_atr is not None and risk_atr <= params.max_risk_atr,
    }
    failed = next((name for name in RULES if not checks[name]), "pass")
    return {
        "bar_time": bar.ts_utc.isoformat(),
        "close": bar.close,
        "ema200": ema,
        "adx14": adx,
        "plus_di": plus,
        "minus_di": minus,
        "vwap": vwap,
        "atr14": atr,
        "distance_atr": distance,
        "risk_atr": risk_atr,
        "ha_state": ha_state,
        "first_failed_rule": failed,
        "passes_all": failed == "pass",
        **{f"{name}_pass": checks[name] for name in RULES},
    }


def _rows_for_basis(
    entries: Sequence[OwnerEntry], chart: tuple[Candle, ...], basis: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_time = {bar.ts_utc: index for index, bar in enumerate(chart)}
    rows: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    contexts: dict[tuple[str, str, str, str], RuleContext] = {}
    for variant in VARIANTS:
        anchor, ha_mode, adx_mode, ema_mode, shift = _variant_parts(variant)
        key = (anchor, ha_mode, adx_mode, ema_mode)
        contexts.setdefault(
            key,
            _context(chart, anchor=anchor, ha_mode=ha_mode, adx_mode=adx_mode, ema_mode=ema_mode),
        )
        context = contexts[key]
        center_pass = 0
        window_pass = 0
        first_fail_counts = {name: 0 for name in (*RULES, "pass", "missing_bar")}
        for entry_number, entry in enumerate(entries, start=1):
            center_index = by_time.get(entry.entry_time)
            if center_index is None:
                first_fail_counts["missing_bar"] += 1
                continue
            entry_window: list[dict[str, Any]] = []
            for offset in OFFSETS:
                index = center_index + offset + shift
                if index < 0 or index >= len(chart):
                    continue
                evaluated = _evaluate(context, index, entry.direction)
                row = {
                    "entry": entry_number,
                    "entry_time": entry.entry_time.isoformat(),
                    "direction": entry.direction,
                    "owner_entry_price": entry.entry_price,
                    "basis": basis,
                    "variant": variant,
                    "offset": offset,
                    "bar_index": index,
                    **evaluated,
                }
                rows.append(row)
                entry_window.append(evaluated)
            center = next(
                (row for row in rows[::-1] if row["entry"] == entry_number and row["offset"] == 0),
                None,
            )
            if center and center["passes_all"]:
                center_pass += 1
            if any(item["passes_all"] for item in entry_window):
                window_pass += 1
            first = center["first_failed_rule"] if center else "missing_bar"
            first_fail_counts[first] += 1
        summaries[variant] = {
            "center_pass": center_pass,
            "window_pass": window_pass,
            "first_failed_rule": first_fail_counts,
        }
    return rows, summaries


def _report(
    entries: Sequence[OwnerEntry], basis_summaries: dict[str, dict[str, Any]], generated_at: str
) -> str:
    mid = basis_summaries.get("mid", {})
    variants = mid.get("variants", {})
    best = max(
        variants.items(),
        key=lambda item: item[1]["window_pass"],
        default=("none", {"window_pass": 0}),
    )
    baseline_name = "forming_wilder_sma_ema_utc"
    lines = [
        "# T-157 Tai rule-by-rule replay diagnostic",
        "",
        f"Generated: `{generated_at}`; owner entries: **{len(entries)}**.",
        "",
        "This diagnostic compares entry **times and directions** only. It never compares the owner's NQ1! price to the NAS100 CFD price.",  # noqa: E501
        "",
        "## Baseline and timing result",
        "",
        f"Mid-price baseline (`{baseline_name}`) center-bar passes: **{variants.get(baseline_name, {}).get('center_pass', 0)}/31**.",  # noqa: E501
        f"Mid-price baseline passes somewhere in ±3 bars: **{variants.get(baseline_name, {}).get('window_pass', 0)}/31**.",  # noqa: E501
        f"Best registered switch: `{best[0]}` with **{best[1]['window_pass']}/31** entries passing somewhere in ±3 bars.",  # noqa: E501
        "",
        "The first failed rule is evaluated in this order: `ha_state`, `ema_side`, `adx_di`, `vwap_side`, `dont_chase`, `risk_atr`.",  # noqa: E501
        "",
        "## Switch counts on mid-price data",
        "",
        "| Variant | Center all-pass | Any pass in ±3 bars |",
        "| --- | ---: | ---: |",
    ]
    for name, summary in variants.items():
        lines.append(f"| `{name}` | {summary['center_pass']} | {summary['window_pass']} |")
    lines += [
        "",
        "## Price-basis baseline counts",
        "",
        "| Basis | Center all-pass | Any pass in ±3 bars |",
        "| --- | ---: | ---: |",
    ]
    for basis, payload in basis_summaries.items():
        baseline = payload["variants"].get(baseline_name, {})
        lines.append(
            f"| {basis} | {baseline.get('center_pass', 0)} | {baseline.get('window_pass', 0)} |"
        )
    lines += [
        "",
        "## Baseline first-failure counts",
        "",
        "The complete Pod CSV has one row for every available entry/basis/variant/offset. The committed repository CSV is a compact center-bar index; rerun this script to regenerate the full 5,104-row artifact.",  # noqa: E501
        "",
    ]
    baseline = variants.get(baseline_name, {})
    for name, count in baseline.get("first_failed_rule", {}).items():
        lines.append(f"- `{name}`: {count}")
    lines += [
        "",
        "## Suspected implementation differences tested",
        "",
        "- 54m Heikin Ashi: closed versus forming values, with UTC and CME-open grouping.",
        "- VWAP: UTC midnight, Riyadh midnight, and CME-open session resets.",
        "- ADX/DI smoothing: production Wilder smoothing versus simple rolling windows.",
        "- EMA seed: production complete-SMA seed versus first-close seed.",
        "- Bar timestamp convention: center entry bar shifted one 3m bar earlier or later.",
        "",
        "No parameters were tuned. If the best registered switch does not reach 20/31, the acceptance stop rule applies and no additional combinations are tried.",  # noqa: E501
        "",
        "Safety: broker calls 0, orders 0, sealed holdout read false, and no gate, cost, ledger, promotion or lake writes.",  # noqa: E501
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument(
        "--owner-file",
        type=Path,
        default=Path("docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv"),
    )
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/t157_rule_by_rule.csv"))
    parser.add_argument("--report", type=Path, default=Path("docs/evidence/t157_report.md"))
    args = parser.parse_args()
    entries = read_owner_entries(args.owner_file)
    if len(entries) != 31:
        raise RuntimeError(f"expected 31 owner positions, found {len(entries)}")
    start = min(entry.entry_time for entry in entries) - timedelta(days=2)
    end = max(entry.entry_time for entry in entries) + timedelta(minutes=15)
    minutes_by_basis = {"mid": read_nas100_minutes(args.lake, start, end)}
    for basis in ("bid", "ask"):
        side_minutes = _read_side_minutes(args.lake, start, end, basis)
        if side_minutes:
            minutes_by_basis[basis] = side_minutes
    rows: list[dict[str, Any]] = []
    basis_summaries: dict[str, dict[str, Any]] = {}
    for basis, minutes in minutes_by_basis.items():
        if not minutes:
            continue
        chart = aggregate_3m(minutes)
        basis_rows, summaries = _rows_for_basis(entries, chart, basis)
        rows.extend(basis_rows)
        basis_summaries[basis] = {
            "variants": summaries,
            "chart_bars": len(chart),
            "source_minutes": len(minutes),
        }
    if not rows:
        raise RuntimeError("NAS100 1m Capital.com data is missing")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for name in row:
            if name not in fieldnames:
                fieldnames.append(name)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    generated_at = datetime.now(UTC).isoformat()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_report(entries, basis_summaries, generated_at), encoding="utf-8")
    print(
        json.dumps(
            {
                "task": "T-157",
                "entries": len(entries),
                "rows": len(rows),
                "bases": list(basis_summaries),
                "mid_variants": basis_summaries.get("mid", {}).get("variants", {}),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
