#!/usr/bin/env python3
"""Run the pre-registered T-148 exit and mirror diagnostic.

The runner is deliberately isolated from promotion and broker code.  It reads
only the unsealed training lake and writes diagnostic evidence.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from scripts.lab_by_year import PERIODS, _load_window, candidate_manifest
from scripts.lab_recent_zero_cost import _candidate_specs, spec_hash
from scripts.lab_tai_smart_filter import TaiParams, _passes, _risk, build_features

from trader.analysis.indicators import atr, session_tags
from trader.data.resample import Candle, resample_ohlc
from trader.lab.backtest import _exit_rules
from trader.lab.engine import IndicatorCache, evaluate_entries

SCHEMES = ("E0", "E1", "E2", "E3", "E4", "E5", "E6")
GROUPS = (
    ("NSXUSD", 1),
    ("NSXUSD", 3),
    ("NSXUSD", 5),
    ("NSXUSD", 60),
    ("XAUUSD", 1),
    ("XAUUSD", 3),
    ("XAUUSD", 5),
    ("XAUUSD", 60),
)
END_HOLDOUT = datetime(2026, 7, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Entry:
    index: int
    time: datetime
    direction: str
    price: float
    risk: float
    stop: float
    target: float | None
    time_stop: int | None
    atr_value: float


@dataclass(frozen=True, slots=True)
class Closed:
    entry: Entry
    exit_index: int
    exit_time: datetime
    r: float
    reason: str


def stamp() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(temp, path)


def rss_bytes() -> int | None:
    try:
        return int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1]) * os.sysconf(
            "SC_PAGE_SIZE"
        )
    except (OSError, ValueError, IndexError):
        return None


def stats(trades: list[Closed], bars: tuple[Candle, ...], cost: float) -> dict[str, Any]:
    values = [trade.r * trade.entry.risk - cost for trade in trades]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(values),
        "wins": sum(value > 0 for value in values),
        "win_rate": sum(value > 0 for value in values) / len(values) if values else 0.0,
        "net_points": sum(values),
        "total_r": sum(trade.r - cost / trade.entry.risk for trade in trades),
        "profit_factor": gains / losses if losses else ("inf" if gains else 0.0),
        "max_drawdown_points": drawdown,
        "average_r": (sum(trade.r - cost / trade.entry.risk for trade in trades) / len(values))
        if values
        else 0.0,
    }


def _hit(bar: Candle, direction: str, level: float, *, target: bool) -> tuple[bool, float]:
    if direction == "long":
        if bar.open >= level if target else bar.open <= level:
            return True, bar.open
        if bar.high >= level if target else bar.low <= level:
            return True, level
    else:
        if bar.open <= level if target else bar.open >= level:
            return True, bar.open
        if bar.low <= level if target else bar.high >= level:
            return True, level
    return False, level


def simulate(bars: tuple[Candle, ...], entries: list[Entry], scheme: str) -> list[Closed]:
    """Simulate one exit scheme with stop-first intrabar ordering."""
    out: list[Closed] = []
    cursor = 0
    last_exit = -1
    while cursor < len(entries):
        entry = entries[cursor]
        cursor += 1
        if entry.index <= last_exit:
            continue
        direction = entry.direction
        remaining = 1.0
        realized = 0.0
        stop = entry.stop
        targets = (1.0, 3.0) if scheme == "E6" else (1.0, 2.0, 3.0)
        hit_targets: set[float] = set()
        fractions = (0.5, 0.5) if scheme == "E6" else (1 / 3, 1 / 3, 1 / 3)
        stop_after = 1 if scheme in {"E4", "E6"} else 2 if scheme == "E5" else 99
        for index in range(entry.index, len(bars)):
            bar = bars[index]
            stop_hit, stop_price = _hit(bar, direction, stop, target=False)
            if stop_hit:
                move = (
                    ((stop_price - entry.price) / entry.risk)
                    if direction == "long"
                    else ((entry.price - stop_price) / entry.risk)
                )
                realized += remaining * move
                out.append(Closed(entry, index, bar.ts_utc, realized, "stop"))
                last_exit = index
                break
            hit: list[tuple[float, float]] = []
            if scheme in {"E1", "E2", "E3"}:
                multiple = float(scheme[1:])
                level = (
                    entry.price + multiple * entry.risk
                    if direction == "long"
                    else entry.price - multiple * entry.risk
                )
                ok, price = _hit(bar, direction, level, target=True)
                if ok:
                    realized = (
                        ((price - entry.price) / entry.risk)
                        if direction == "long"
                        else ((entry.price - price) / entry.risk)
                    )
                    out.append(Closed(entry, index, bar.ts_utc, realized, "target"))
                    last_exit = index
                    break
            elif scheme == "E0":
                if entry.target is not None:
                    ok, price = _hit(bar, direction, entry.target, target=True)
                    if ok:
                        realized = (
                            ((price - entry.price) / entry.risk)
                            if direction == "long"
                            else ((entry.price - price) / entry.risk)
                        )
                        out.append(Closed(entry, index, bar.ts_utc, realized, "target"))
                        last_exit = index
                        break
            else:
                for multiple, fraction in zip(targets, fractions, strict=True):
                    if multiple in hit_targets:
                        continue
                    if scheme == "E6" and multiple == 2:
                        continue
                    if scheme == "E6" and multiple == 3:
                        multiple = 3.0
                    level = (
                        entry.price + multiple * entry.risk
                        if direction == "long"
                        else entry.price - multiple * entry.risk
                    )
                    ok, price = _hit(bar, direction, level, target=True)
                    if ok:
                        realized += fraction * multiple
                        remaining -= fraction
                        hit_targets.add(multiple)
                        hit.append((multiple, price))
                if hit:
                    highest = max(item[0] for item in hit)
                    if highest >= stop_after:
                        stop = entry.price
                    if (scheme == "E6" and 3.0 in hit_targets) or (
                        scheme != "E6" and 3.0 in hit_targets
                    ):
                        out.append(Closed(entry, index, bar.ts_utc, realized, "target"))
                        last_exit = index
                        break
            held = index - entry.index
            if entry.time_stop is not None and held >= entry.time_stop:
                move = (
                    ((bar.close - entry.price) / entry.risk)
                    if direction == "long"
                    else ((entry.price - bar.close) / entry.risk)
                )
                realized += remaining * move
                out.append(Closed(entry, index, bar.ts_utc, realized, "time_stop"))
                last_exit = index
                break
        else:
            bar = bars[-1]
            move = (
                ((bar.close - entry.price) / entry.risk)
                if direction == "long"
                else ((entry.price - bar.close) / entry.risk)
            )
            out.append(
                Closed(entry, len(bars) - 1, bar.ts_utc, realized + remaining * move, "end_of_data")
            )
            last_exit = len(bars) - 1
    return out


def candidate_entries(spec: Any, bars: tuple[Candle, ...]) -> list[Entry]:
    cache = IndicatorCache.from_candles(bars, spec.timeframe_minutes)
    chart = cache.bars
    signals = evaluate_entries(spec, chart, indicator_cache=cache)
    by_time = {signal.timestamp_utc: signal for signal in signals}
    atr_values = atr(chart, 14)
    entries: list[Entry] = []
    for index in range(1, len(chart)):
        signal = by_time.get(chart[index - 1].ts_utc)
        if signal is None:
            continue
        av = atr_values[index - 1] or 1.0
        stop, target, time_stop, _, _ = _exit_rules(spec, av)
        risk_distance = float(stop)
        stop_price = (
            chart[index].open - risk_distance
            if spec.direction == "long"
            else chart[index].open + risk_distance
        )
        target_price = (
            chart[index].open + float(target)
            if spec.direction == "long" and target is not None
            else chart[index].open - float(target)
            if target is not None
            else None
        )
        entries.append(
            Entry(
                index,
                chart[index].ts_utc,
                spec.direction,
                chart[index].open,
                risk_distance,
                stop_price,
                target_price,
                time_stop,
                av,
            )
        )
    return entries


def tai_entries(
    minute_candles: tuple[Candle, ...], timeframe: int, mirror: bool = False
) -> tuple[tuple[Candle, ...], list[Entry]]:
    params = TaiParams()
    features = build_features(minute_candles, timeframe, params)
    entries: list[Entry] = []
    last_exit = -10_000
    for index, bar in enumerate(features.bars):
        direction = features.signals[index]
        if direction is None or index - last_exit < params.refresh_bars:
            continue
        if mirror:
            direction = "short" if direction == "long" else "long"
        if not _passes(features, index, direction, params):
            continue
        risk_data = _risk(features, index, direction, params)
        if risk_data is None:
            continue
        stop, risk = risk_data
        stop_price = bar.close - risk if direction == "long" else bar.close + risk
        target_price = (
            bar.close + params.target_multiple * risk
            if direction == "long"
            else bar.close - params.target_multiple * risk
        )
        entries.append(
            Entry(
                index,
                bar.ts_utc,
                direction,
                bar.close,
                risk,
                stop_price,
                target_price,
                None,
                features.atr[index] or 1.0,
            )
        )
    return features.bars, entries


def bucket_summary(e0: list[Closed], e4: list[Closed], cost: float) -> dict[str, Any]:
    buckets: dict[str, dict[str, list[Closed]]] = {
        "session": defaultdict(list),
        "volatility": defaultdict(list),
        "trend_range": defaultdict(list),
    }
    for trade in e0 + e4:
        session = next(iter(session_tags(trade.entry.time)), "closed")
        buckets["session"][session].append(trade)
        buckets["volatility"]["high" if trade.entry.atr_value >= 2 else "normal"].append(trade)
        buckets["trend_range"]["trend" if trade.entry.atr_value >= 1 else "range"].append(trade)
    ordered = sorted(e0 + e4, key=lambda trade: trade.entry.atr_value)
    tercile_size = max(1, (len(ordered) + 2) // 3)
    volatility_tercile = defaultdict(list)
    for index, trade in enumerate(ordered):
        bucket = min(index // tercile_size, 2) + 1
        volatility_tercile[f"T{bucket}"].append(trade)
    buckets["volatility_tercile"] = volatility_tercile
    result: dict[str, Any] = {}
    for kind, values in buckets.items():
        result[kind] = {
            name: {
                "trades": len(items),
                "e0": stats([x for x in e0 if x in items], (), cost),
                "e4": stats([x for x in e4 if x in items], (), cost),
            }
            for name, items in values.items()
            if len(items) >= 30
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument(
        "--t144", type=Path, default=Path("/workspace/state/lab/t144_5m_3m_top10_summary.json")
    )
    parser.add_argument("--t143", type=Path, default=Path("/workspace/state/lab/t143_merged.json"))
    parser.add_argument(
        "--t145-summary", type=Path, default=Path("docs/evidence/t145_by_year_summary.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--memory-limit-gb", type=float, default=13.0)
    args = parser.parse_args()
    manifest = candidate_manifest(args.t144, args.t143)
    payload: dict[str, Any] = {
        "task": "T-148",
        "generated_at": stamp(),
        "diagnostic_only": True,
        "sealed_holdout_read": False,
        "gate1_evaluated": False,
        "broker_calls": 0,
        "orders": 0,
        "trial_total": (len(manifest) + len(GROUPS)) * len(SCHEMES),
        "preflight": {
            "cpu_count": os.cpu_count(),
            "rss_start_bytes": rss_bytes(),
            "memory_limit_gb": args.memory_limit_gb,
        },
        "groups": {},
    }
    for instrument, timeframe in GROUPS:
        group = f"{instrument}:{timeframe}"
        rows = [row for row in manifest if row["group"] == group]
        all_specs = _candidate_specs(
            instrument, timeframe, staged_generations=3, max_staged_parents=10
        )
        by_hash = {spec_hash(row["spec"]): row["spec"] for row in all_specs}
        candles = _load_window(
            args.lake, instrument, timeframe, start=PERIODS["2022"][0], end=END_HOLDOUT
        )
        chart = tuple(bar for bar in resample_ohlc(candles, timeframe) if bar.complete)
        tai_chart, tai_es = tai_entries(candles, timeframe)
        mirror_chart, mirror_es = tai_entries(candles, timeframe, mirror=True)
        candidate_rows = rows + [
            {
                "group": group,
                "rank": 25,
                "strategy_hash": "tai_baseline",
                "source": "T-147",
                "rules": "Tai Smart Filter exact rules",
            }
        ]
        group_out: dict[str, Any] = {
            "instrument": instrument,
            "timeframe": timeframe,
            "candidates": {},
            "paired": {},
            "paired_summary": {},
            "market_conditions": {},
            "mirror_full": {},
        }
        for row in candidate_rows:
            digest = row["strategy_hash"]
            entry_source = (
                tai_es if digest == "tai_baseline" else candidate_entries(by_hash[digest], candles)
            )
            periods: dict[str, Any] = {}
            for period, (start, end) in PERIODS.items():
                selected = [e for e in entry_source if start <= e.time < end]
                scheme_stats: dict[str, Any] = {}
                for scheme in SCHEMES:
                    closed = simulate(
                        chart if digest != "tai_baseline" else tai_chart, selected, scheme
                    )
                    scheme_stats[scheme] = {
                        "zero_cost": stats(closed, chart, 0.0),
                        "normal_cost": stats(closed, chart, 4.0 if instrument == "NSXUSD" else 1.6),
                    }
                periods[period] = scheme_stats
            group_out["candidates"][digest] = {
                "rank": row.get("rank"),
                "source": row.get("source"),
                "strategy_hash": digest,
                "periods": periods,
            }
            e0 = simulate(chart if digest != "tai_baseline" else tai_chart, entry_source, "E0")
            e4 = simulate(chart if digest != "tai_baseline" else tai_chart, entry_source, "E4")
            group_out["market_conditions"][digest] = bucket_summary(
                e0, e4, 4.0 if instrument == "NSXUSD" else 1.6
            )
        for period, (start, end) in PERIODS.items():
            selected = [entry for entry in mirror_es if start <= entry.time < end]
            closed = simulate(mirror_chart, selected, "E0")
            cost = 4.0 if instrument == "NSXUSD" else 1.6
            group_out["mirror_full"][period] = {
                "zero_cost": stats(closed, mirror_chart, 0.0),
                "normal_cost": stats(closed, mirror_chart, cost),
            }
        for digest, candidate in group_out["candidates"].items():
            comparisons: dict[str, Any] = {}
            pairs = {
                "E4_minus_E0": ("E4", "E0"),
                "E4_minus_E2": ("E4", "E2"),
                "E5_minus_E4": ("E5", "E4"),
                "E6_minus_E4": ("E6", "E4"),
            }
            for period, schemes in candidate["periods"].items():
                comparisons[period] = {
                    name: {
                        mode: {
                            "delta_total_r": schemes[left][mode]["total_r"]
                            - schemes[right][mode]["total_r"],
                            "delta_net_points": schemes[left][mode]["net_points"]
                            - schemes[right][mode]["net_points"],
                            "trades_equal": schemes[left][mode]["trades"]
                            == schemes[right][mode]["trades"],
                        }
                        for mode in ("zero_cost", "normal_cost")
                    }
                    for name, (left, right) in pairs.items()
                }
            group_out["paired"][digest] = comparisons
        for name, (left, right) in pairs.items():
            group_out["paired_summary"][name] = {}
            for mode in ("zero_cost", "normal_cost"):
                changes = [
                    candidate["periods"][period][left][mode]["total_r"]
                    - candidate["periods"][period][right][mode]["total_r"]
                    for candidate in group_out["candidates"].values()
                    for period in PERIODS
                ]
                point_changes = [
                    candidate["periods"][period][left][mode]["net_points"]
                    - candidate["periods"][period][right][mode]["net_points"]
                    for candidate in group_out["candidates"].values()
                    for period in PERIODS
                ]
                group_out["paired_summary"][name][mode] = {
                    "observations": len(changes),
                    "improved": sum(change > 0 for change in changes),
                    "median_delta_total_r": median(changes) if changes else 0.0,
                    "median_delta_net_points": median(point_changes) if point_changes else 0.0,
                }
        payload["groups"][group] = group_out
        print(f"completed {group} rss={rss_bytes()}", flush=True)
        if rss_bytes() and rss_bytes() > args.memory_limit_gb * 1024**3:
            raise RuntimeError("RSS exceeded registered cap")
        del candles, chart, tai_chart
        gc.collect()
    payload["preflight"]["rss_end_bytes"] = rss_bytes()
    write_json(args.output, payload)
    lines = [
        "# T-148 exit study",
        "",
        "Diagnostic only: no holdout, Gate 1, broker, orders, ledger or promotion writes.",
        "",
        f"Generated `{payload['generated_at']}`; trial total {payload['trial_total']} "
        "(24 T-145 candidates plus one Tai baseline per group, seven schemes).",
        "",
        "| Group | Candidate | Period | E0 normal R | E4 normal R | E4 minus E0 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for group, data in payload["groups"].items():
        for digest, candidate in data["candidates"].items():
            for period, schemes in candidate["periods"].items():
                e0 = schemes["E0"]["normal_cost"]["total_r"]
                e4 = schemes["E4"]["normal_cost"]["total_r"]
                lines.append(
                    f"| {group} | {digest} | {period} | {e0:.3f} | {e4:.3f} | {e4 - e0:.3f} |"
                )
    lines += [
        "",
        "## Exit schemes",
        "",
        "E4 is the owner's scale-out (thirds at 1R/2R/3R with stop to entry after 1R). "
        "E5 moves to entry after 2R; E6 uses halves at 1R and 3R. "
        "Stops are evaluated before targets on the same bar.",
        "",
        "## Market conditions",
        "",
        "The JSON contains the four pre-registered paired comparisons and the full-inversion "
        "Tai mirror for every group and period.",
        "Only market-condition buckets with at least 30 trades are reported in the JSON.",
        "",
        "## Plain-language reading",
        "",
        "Positive deltas mean the left-hand exit scheme improved the paired total-R result "
        "for the same entries; negative deltas mean it hurt. The bucketed E0 versus E4 "
        "rows show where the scale-out helped or hurt, with stop-first ordering when a "
        "stop and target share a bar.",
    ]
    args.table.parent.mkdir(parents=True, exist_ok=True)
    args.table.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
