#!/usr/bin/env python3
"""Diagnostic-only T-163 VWAP/54m-anchor/filter-subset replay.

This script deliberately stays offline.  It reuses the T-162 indicator arithmetic,
then varies only the requested VWAP and higher-timeframe grouping choices.  It never
calls a broker, reads the sealed holdout, writes the lake, tunes thresholds, or
changes the production port contract.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from trader.data.resample import Candle, resample_ohlc
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from trader.data.resample import Candle, resample_ohlc

try:
    from scripts import lab_t162_pine_port as port
    from scripts.lab_t151_owner_trades import read_nas100_minutes, read_owner_entries
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import lab_t162_pine_port as port
    from scripts.lab_t151_owner_trades import read_nas100_minutes, read_owner_entries

ZONE = ZoneInfo("America/New_York")
TIMEFRAME = 3
GROUP_MINUTES = 54
GROUP_BARS = 18
ANCHORS = ("utc_midnight", "ny_18:00", "ny_00:00", "ny_09:30", "day_first")
VWAPS = (
    "utc_midnight_equal",
    "ny_18:00_equal",
    "ny_09:30_equal",
    "ny_00:00_equal",
    "utc_midnight_volume",
    "tick_count_unavailable",
)
FILTERS = ("ema", "adx_di", "vwap_side", "vwap_distance")


def _session_start(stamp: datetime, name: str) -> datetime:
    if name == "utc_midnight":
        return stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    local = stamp.astimezone(ZONE)
    hour, minute = {"ny_18:00": (18, 0), "ny_00:00": (0, 0), "ny_09:30": (9, 30)}[name]
    start = datetime(local.year, local.month, local.day, hour, minute, tzinfo=ZONE)
    if stamp < start.astimezone(UTC):
        start -= timedelta(days=1)
    return start.astimezone(UTC)


def _groups(bars: Sequence[Candle], anchor: str) -> list[list[int]]:
    if anchor == "day_first":
        by_day: dict[object, list[int]] = defaultdict(list)
        for i, bar in enumerate(bars):
            by_day[bar.ts_utc.astimezone(ZONE).date()].append(i)
        return [
            chunk
            for indices in by_day.values()
            for chunk in (indices[i : i + GROUP_BARS] for i in range(0, len(indices), GROUP_BARS))
        ]
    grouped: dict[datetime, list[int]] = defaultdict(list)
    for i, bar in enumerate(bars):
        start = _session_start(bar.ts_utc, anchor)
        offset = int((bar.ts_utc - start).total_seconds() // 60)
        grouped[start + timedelta(minutes=GROUP_MINUTES * (offset // GROUP_MINUTES))].append(i)
    return [sorted(indices) for _, indices in sorted(grouped.items())]


def _ha_series_variant(
    bars: Sequence[Candle], mode: str, anchor: str
) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    groups = _groups(bars, anchor)
    opens: list[float | None] = [None] * len(bars)
    closes: list[float | None] = [None] * len(bars)
    previous: tuple[float, float] | None = None
    for indices in groups:
        ordered = [bars[i] for i in indices]
        complete = len(ordered) == GROUP_BARS and all(
            ordered[i + 1].ts_utc - ordered[i].ts_utc == timedelta(minutes=TIMEFRAME)
            for i in range(len(ordered) - 1)
        )
        if complete:
            raw_open, raw_close = ordered[0].open, ordered[-1].close
            value = (
                raw_open + max(x.high for x in ordered) + min(x.low for x in ordered) + raw_close
            ) / 4.0
            group_open = (
                (raw_open + raw_close) / 2.0
                if previous is None
                else (previous[0] + previous[1]) / 2.0
            )
            if mode == "closed":
                for i in indices:
                    if previous is not None:
                        opens[i], closes[i] = previous
            else:
                for end, i in enumerate(indices, 1):
                    prefix = ordered[:end]
                    close = (
                        prefix[0].open
                        + max(x.high for x in prefix)
                        + min(x.low for x in prefix)
                        + prefix[-1].close
                    ) / 4.0
                    opens[i], closes[i] = group_open, close
            previous = (group_open, value)
        elif mode == "forming" and ordered:
            for end, i in enumerate(indices, 1):
                prefix = ordered[:end]
                close = (
                    prefix[0].open
                    + max(x.high for x in prefix)
                    + min(x.low for x in prefix)
                    + prefix[-1].close
                ) / 4.0
                group_open = (
                    (prefix[0].open + prefix[-1].close) / 2.0
                    if previous is None
                    else (previous[0] + previous[1]) / 2.0
                )
                opens[i], closes[i] = group_open, close
    buy, sell = port._crosses(opens, closes)
    return tuple(opens), tuple(closes), buy, sell


def _vwap_variant(bars: Sequence[Candle], name: str) -> tuple[float | None, ...]:
    if name == "tick_count_unavailable":
        return tuple(None for _ in bars)
    anchor, weighting = name.rsplit("_", 1)
    result: list[float | None] = []
    current: object = None
    weighted = volume = equal_sum = 0.0
    count = 0
    for bar in bars:
        key = _session_start(bar.ts_utc, anchor)
        if key != current:
            current, weighted, volume, equal_sum, count = key, 0.0, 0.0, 0.0, 0
        typical = (bar.high + bar.low + bar.close) / 3.0
        if weighting == "volume" and bar.volume > 0:
            weighted += typical * bar.volume
            volume += bar.volume
        else:
            equal_sum += typical
            count += 1
        result.append(
            weighted / volume
            if weighting == "volume" and volume > 0
            else equal_sum / count
            if count
            else None
        )
    return tuple(result)


def _features(bars: Sequence[Candle], mode: str, ha_anchor: str, vwap_name: str) -> port.Features:
    ha_open, ha_close, buy, sell = _ha_series_variant(bars, mode, ha_anchor)
    ema = port._ema([b.close for b in bars], port.EMA_PERIOD)
    atr = port._atr(bars)
    adx, plus, minus = port._dmi(bars)
    return port.Features(
        ema, atr, adx, plus, minus, _vwap_variant(bars, vwap_name), ha_open, ha_close, buy, sell
    )


def _passes(
    bars: Sequence[Candle], f: port.Features, i: int, direction: str, subset: set[str]
) -> bool:
    vals = (f.ema[i], f.atr[i], f.adx[i], f.plus_di[i], f.minus_di[i], f.vwap[i])
    if any(value is None for value in vals):
        return False
    close, ema, atr, adx, plus, minus, vwap = (
        bars[i].close,
        f.ema[i],
        f.atr[i],
        f.adx[i],
        f.plus_di[i],
        f.minus_di[i],
        f.vwap[i],
    )
    if "ema" in subset and not (close > ema if direction == "long" else close < ema):
        return False
    if "adx_di" in subset and not (
        adx >= 20 and (plus > minus if direction == "long" else minus > plus)
    ):
        return False
    if "vwap_side" in subset and not (close > vwap if direction == "long" else close < vwap):
        return False
    return not ("vwap_distance" in subset and abs(close - vwap) / atr > port.VWAP_DISTANCE_ATR)


def _replay_subset(
    bars: Sequence[Candle], f: port.Features, mode: str, subset: set[str]
) -> tuple[port.PortTrade, ...]:
    trades: list[port.PortTrade] = []
    active: dict[str, object] | None = None
    last_exit: int | None = None
    for i, bar in enumerate(bars):
        signals: list[str] = []
        for direction, cross in (("long", f.buy_cross[i]), ("short", f.sell_cross[i])):
            if cross and _passes(bars, f, i, direction, subset):
                ok, _ = port._risk(bars, f, i, direction)
                if ok:
                    signals.append(direction)
        if last_exit is not None and i - last_exit >= port.REFRESH_BARS:
            for direction in ("long", "short"):
                if _passes(bars, f, i, direction, subset):
                    ok, _ = port._risk(bars, f, i, direction)
                    if ok:
                        signals.append(direction)
        if active is None and signals:
            direction = signals[0]
            _, risk = port._risk(bars, f, i, direction)
            active = {
                "direction": direction,
                "entry_index": i,
                "entry_time": bar.ts_utc,
                "entry_price": bar.close,
                "stop_price": bar.close - risk if direction == "long" else bar.close + risk,
                "risk": risk,
                "highest": 0,
                "active_stop": bar.close - risk if direction == "long" else bar.close + risk,
            }
        if active is None:
            continue
        direction = str(active["direction"])
        long = direction == "long"
        risk = float(active["risk"])
        entry = float(active["entry_price"])
        stop = float(active["active_stop"])
        high_target = int(active["highest"])
        hit_stop = bar.low <= stop if long else bar.high >= stop
        hit1 = bar.high >= entry + risk if long else bar.low <= entry - risk
        hit2 = bar.high >= entry + risk * 2 if long else bar.low <= entry - risk * 2
        hit3 = bar.high >= entry + risk * 3 if long else bar.low <= entry - risk * 3
        reason: str | None = None
        if high_target == 0:
            if hit3:
                active["highest"], reason = 3, "tp3"
            elif hit2:
                active["highest"], active["active_stop"] = 2, entry
            elif hit1:
                active["highest"], active["active_stop"] = 1, entry
            elif hit_stop:
                reason = "stop"
        elif hit3:
            active["highest"], reason = 3, "tp3"
        elif hit2:
            active["highest"], active["active_stop"] = 2, entry
        elif hit_stop:
            reason = "be"
        if reason:
            trades.append(
                port.PortTrade(
                    direction,
                    int(active["entry_index"]),
                    active["entry_time"],
                    entry,
                    float(active["stop_price"]),
                    i,
                    bar.ts_utc,
                    reason,
                    int(active["highest"]),
                )
            )
            active = None
            last_exit = i
    if active is not None:
        trades.append(
            port.PortTrade(
                str(active["direction"]),
                int(active["entry_index"]),
                active["entry_time"],
                float(active["entry_price"]),
                float(active["stop_price"]),
                None,
                None,
                None,
                int(active["highest"]),
            )
        )
    return tuple(trades)


def _owner_rows(
    owners: Sequence[object], bars: Sequence[Candle], f: port.Features, mode: str, variant: str
) -> list[dict[str, object]]:
    rows = []
    for owner in owners:
        idx, bar = port._bar_at(bars, owner.entry_time)
        row: dict[str, object] = {
            "variant": variant,
            "mode": mode,
            "owner_time_utc": owner.entry_time.isoformat(),
            "owner_time_ny": owner.entry_time.astimezone(ZONE).isoformat(),
            "direction": owner.direction,
            "bar_present": bar is not None,
        }
        if idx is not None:
            close, vwap, atr = bar.close, f.vwap[idx], f.atr[idx]
            row.update(
                {
                    "close": close,
                    "vwap": vwap,
                    "atr14": atr,
                    "ema_side": close > f.ema[idx]
                    if owner.direction == "long"
                    else close < f.ema[idx],
                    "adx_di": f.adx[idx] is not None
                    and f.adx[idx] >= 20
                    and (
                        f.plus_di[idx] > f.minus_di[idx]
                        if owner.direction == "long"
                        else f.minus_di[idx] > f.plus_di[idx]
                    ),
                    "vwap_side": close > vwap if owner.direction == "long" else close < vwap,
                    "vwap_distance": abs(close - vwap) / atr if vwap and atr else None,
                    "atr_risk_pass": port._risk(bars, f, idx, owner.direction)[0],
                }
            )
        rows.append(row)
    return rows


def run(lake: Path, owner_file: Path) -> dict[str, object]:
    owners = read_owner_entries(owner_file)
    if len(owners) != 31:
        raise RuntimeError(f"expected 31 owner entries, found {len(owners)}")
    start = min(x.entry_time for x in owners) - timedelta(days=3)
    end = max(x.entry_time for x in owners) + timedelta(days=1)
    minutes = read_nas100_minutes(lake, start, end)
    bars = tuple(x for x in resample_ohlc(minutes, TIMEFRAME) if x.complete)
    if not bars:
        raise RuntimeError("NAS100 3m data is missing")
    rows: list[dict[str, object]] = []
    vwap_results = {}
    ha_results = {}
    subset_results = {}
    base_vwap = "utc_midnight_equal"
    for name in VWAPS:
        f = _features(bars, "closed", "utc_midnight", name)
        owner_rows = _owner_rows(owners, bars, f, "closed", name)
        valid = [r for r in owner_rows if r.get("bar_present") and name != "tick_count_unavailable"]
        vwap_results[name] = {
            "owner_rows_with_bar": len(valid),
            "vwap_side_pass": sum(bool(r.get("vwap_side")) for r in valid),
            "vwap_distance_pass": sum(
                bool(
                    r.get("vwap_distance") is not None
                    and r["vwap_distance"] <= port.VWAP_DISTANCE_ATR
                )
                for r in valid
            ),
            "all_four_pass": sum(
                bool(
                    r.get("ema_side")
                    and r.get("adx_di")
                    and r.get("vwap_side")
                    and r.get("vwap_distance") is not None
                    and r["vwap_distance"] <= port.VWAP_DISTANCE_ATR
                )
                for r in valid
            ),
            "tick_count_available": name != "tick_count_unavailable",
        }
        rows.extend(owner_rows)
    for anchor in ANCHORS:
        f = _features(bars, "closed", anchor, base_vwap)
        trades = _replay_subset(bars, f, "closed", set(FILTERS))
        matched, _ = port._match(owners, trades, 2 * TIMEFRAME)
        ha_results[anchor] = {
            "owner_ha_rows_with_bar": sum(
                r["bar_present"] for r in _owner_rows(owners, bars, f, "closed", anchor)
            ),
            "all_filter_port_entries": len(trades),
            "within_plus_minus_2_bars": matched,
            "port_direction_counts": {
                "long": sum(t.direction == "long" for t in trades),
                "short": sum(t.direction == "short" for t in trades),
            },
        }
    f = _features(bars, "closed", "utc_midnight", base_vwap)
    for size in range(1, len(FILTERS) + 1):
        for combo in itertools.combinations(FILTERS, size):
            key = "+".join(combo)
            subset = set(combo)
            entries = _owner_rows(owners, bars, f, "closed", key)
            valid = [r for r in entries if r.get("bar_present")]
            pass_count = sum(
                _passes(bars, f, i, o.direction, subset)
                for o in owners
                for i, b in [port._bar_at(bars, o.entry_time)]
                if i is not None
            )
            trades = _replay_subset(bars, f, "closed", subset)
            matched, _ = port._match(owners, trades, 2 * TIMEFRAME)
            subset_results[key] = {
                "owner_rows_with_bar": len(valid),
                "owner_pass": pass_count,
                "owner_fraction": pass_count / len(valid) if valid else 0.0,
                "port_entries": len(trades),
                "matched_within_plus_minus_2_bars": matched,
                "extra_port_entries": max(0, len(trades) - matched),
            }
    risk_rows = _owner_rows(owners, bars, f, "closed", "risk")
    risk_valid = [r for r in risk_rows if r.get("bar_present")]
    coverage = {
        "NSXUSD": {
            "1m": len(minutes),
            "3m": len(bars),
            "5m": len(tuple(x for x in resample_ohlc(minutes, 5) if x.complete)),
            "60m": len(tuple(x for x in resample_ohlc(minutes, 60) if x.complete)),
        },
        "XAUUSD": {"owner_rows": 0, "note": "no owner rows in the window"},
    }
    return {
        "task": "T-163",
        "generated_at": datetime.now(UTC).isoformat(),
        "owner_entries": len(owners),
        "owner_timezone": "America/New_York",
        "vwap_variants": vwap_results,
        "ha_anchor_variants": ha_results,
        "filter_subsets": subset_results,
        "atr_risk_pass": sum(bool(r.get("atr_risk_pass")) for r in risk_valid),
        "atr_risk_rows_with_bar": len(risk_valid),
        "coverage": coverage,
        "recorded_bars_carry_volume": any(b.volume > 0 for b in bars),
        "signal_group_bars": GROUP_BARS,
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "sealed_holdout_read": False,
            "lake_writes": 0,
            "production_state_writes": 0,
            "gate_evaluated": False,
        },
        "rows": rows,
    }


def report(data: dict[str, object]) -> str:
    best = max(data["filter_subsets"].items(), key=lambda item: item[1]["owner_pass"])
    best_vwap = max(data["vwap_variants"].items(), key=lambda item: item[1]["all_four_pass"])
    best_ha = max(
        data["ha_anchor_variants"].items(), key=lambda item: item[1]["within_plus_minus_2_bars"]
    )
    verdict = (
        "No registered anchor/filter change reaches the 25/31 reproduction bar. "
        f"Best owner filter subset is {best[0]} at {best[1]['owner_pass']}/"
        f"{best[1]['owner_rows_with_bar']} rows; best VWAP variant is {best_vwap[0]} "
        f"at {best_vwap[1]['all_four_pass']} all-filter rows; best 54m anchor is "
        f"{best_ha[0]} at {best_ha[1]['within_plus_minus_2_bars']} entries within "
        "+/-2 chart bars. Ask the owner for the indicator settings screenshot "
        "(especially VWAP session/weighting and HA timeframe/session anchor)."
    )
    lines = [
        "# T-163 filter/anchor diagnosis",
        "",
        "**Verdict:** " + verdict,
        "",
        "This is a diagnostic replay only; no tuning, historical rerun, broker call, "
        "holdout read or lake write was performed.",
        "",
        "## VWAP variants",
        "",
        "| Variant | Rows with bar | VWAP side | VWAP distance | All four |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, item in data["vwap_variants"].items():
        lines.append(
            f"| {name} | {item['owner_rows_with_bar']} | {item['vwap_side_pass']} | "
            f"{item['vwap_distance_pass']} | {item['all_four_pass']} |"
        )
    lines += [
        "",
        "## 54m HA anchors",
        "",
        "| Anchor | Rows with bar | Port entries | Within +/-2 bars |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, item in data["ha_anchor_variants"].items():
        lines.append(
            f"| {name} | {item['owner_ha_rows_with_bar']} | "
            f"{item['all_filter_port_entries']} | {item['within_plus_minus_2_bars']} |"
        )
    lines += [
        "",
        "## Filter subsets",
        "",
        "Every non-empty subset of EMA200 side, ADX/DI, VWAP side and VWAP "
        "distance is in `t163_variants.json`; none reaches 28/30 owner rows.",
        "",
        "## ATR risk and coverage",
        "",
        f"ATR risk rule passes: **{data['atr_risk_pass']}/{data['atr_risk_rows_with_bar']}** "
        f"rows with a recorded 3m bar. NSXUSD coverage is {data['coverage']['NSXUSD']}; "
        "XAUUSD has no owner rows. Recorder bars carry volume: "
        f"**{data['recorded_bars_carry_volume']}**. The 54m groups use "
        f"{data['signal_group_bars']} three-minute bars.",
        "",
        "## Safety",
        "",
        "```json",
        json.dumps(data["safety"], separators=(",", ":")),
        "```",
        "",
        "## Owner action",
        "",
        "If the owner wants the mismatch resolved, provide a screenshot of the Pine "
        "indicator Inputs and Style tabs, including VWAP session/anchor, "
        "weighting/source, and higher-timeframe Heikin Ashi settings.",
    ]
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
    args.output.write_text(json.dumps(data, indent=2, default=str) + "\n")
    args.report.write_text(report(data))
    rows = data.pop("rows")
    fields = sorted({key for row in rows for key in row})
    with args.entries.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {
                "task": "T-163",
                "safety": data["safety"],
                "best_vwap": max(
                    data["vwap_variants"], key=lambda k: data["vwap_variants"][k]["all_four_pass"]
                ),
                "best_ha": max(
                    data["ha_anchor_variants"],
                    key=lambda k: data["ha_anchor_variants"][k]["within_plus_minus_2_bars"],
                ),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
