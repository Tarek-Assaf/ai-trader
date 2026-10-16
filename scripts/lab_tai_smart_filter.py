#!/usr/bin/env python3
"""Run the pre-registered T-147 Tai Trading Smart Filter diagnostic.

This diagnostic is deliberately independent of the promotion pipeline.  It reads
only the unsealed training lake, uses closed candles, writes evidence files, and
never touches a broker, trial ledger, gate configuration, or the sealed holdout.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from trader.analysis import indicators
from trader.data.resample import Candle, resample_ohlc
from trader.lab.runner import load_histdata_candles

END_HOLDOUT = datetime(2026, 7, 1, tzinfo=UTC)
PERIODS = {
    "2022": (datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
    "2023": (datetime(2023, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
    "2024": (datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
    "2025": (datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
    "2026_to_holdout": (datetime(2026, 1, 1, tzinfo=UTC), END_HOLDOUT),
}
INSTRUMENTS = ("NSXUSD", "XAUUSD")
TIMEFRAMES = (1, 3, 5, 60)


@dataclass(frozen=True, slots=True)
class TaiParams:
    signal_multiplier: int = 18
    adx_min: float = 20.0
    vwap_distance_atr: float = 1.5
    swing_lookback: int = 5
    swing_buffer_atr: float = 0.2
    max_risk_atr: float = 2.0
    target_multiple: float = 3.0
    ema_period: int = 200
    adx_period: int = 14
    atr_period: int = 14
    refresh_bars: int = 12


@dataclass(frozen=True, slots=True)
class Features:
    bars: tuple[Candle, ...]
    atr: tuple[float | None, ...]
    ema: tuple[float | None, ...]
    adx: tuple[float | None, ...]
    plus_di: tuple[float | None, ...]
    minus_di: tuple[float | None, ...]
    vwap: tuple[float | None, ...]
    signals: tuple[str | None, ...]


@dataclass(frozen=True, slots=True)
class Trade:
    direction: str
    entry_index: int
    entry_time: datetime
    entry_price: float
    risk_points: float
    real_r: float
    dashboard_r: float
    single_r: dict[str, float]
    exit_index: int
    exit_time: datetime
    exit_reason: str


def stamp() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(temporary, path)


def rss_bytes() -> int | None:
    try:
        return int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def _months_at_or_after(end_date: datetime) -> set[tuple[int, int]]:
    return {
        (year, month)
        for year in range(end_date.year, end_date.year + 3)
        for month in range(1, 13)
        if (year, month) >= (end_date.year, end_date.month)
    }


def load_training_window(lake: Path, instrument: str, timeframe: int) -> tuple[Candle, ...]:
    """Load only 2022 through the day before the sealed holdout boundary."""
    candles = load_histdata_candles(
        lake,
        instrument,
        timeframe,
        exclude_months=_months_at_or_after(END_HOLDOUT),
    )
    return tuple(candle for candle in candles if datetime(2022, 1, 1, tzinfo=UTC) <= candle.ts_utc < END_HOLDOUT)


def heikin_ashi(bars: Iterable[Candle]) -> tuple[Candle, ...]:
    """Build Heikin Ashi bars without looking beyond each closed source bar."""
    result: list[Candle] = []
    for index, bar in enumerate(bars):
        close = (bar.open + bar.high + bar.low + bar.close) / 4.0
        open_price = (bar.open + bar.close) / 2.0 if index == 0 else (result[-1].open + result[-1].close) / 2.0
        result.append(Candle(bar.ts_utc, open_price, max(bar.high, open_price, close), min(bar.low, open_price, close), close, bar.volume, bar.series_key, bar.complete, bar.n_minutes))
    return tuple(result)


def directional_index(bars: tuple[Candle, ...], period: int = 14) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    """Return Wilder +DI and -DI using the same smoothing convention as ADX."""
    if not bars:
        return (), ()
    ranges = indicators.true_range(bars)
    plus = [0.0]
    minus = [0.0]
    for previous, current in zip(bars[:-1], bars[1:], strict=True):
        up = current.high - previous.high
        down = previous.low - current.low
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    result_plus: list[float | None] = [None] * len(bars)
    result_minus: list[float | None] = [None] * len(bars)
    if len(bars) < period:
        return tuple(result_plus), tuple(result_minus)
    smoothed_range = math.fsum(ranges[:period])
    smoothed_plus = math.fsum(plus[:period])
    smoothed_minus = math.fsum(minus[:period])
    for index in range(period - 1, len(bars)):
        if index >= period:
            smoothed_range += ranges[index] - smoothed_range / period
            smoothed_plus += plus[index] - smoothed_plus / period
            smoothed_minus += minus[index] - smoothed_minus / period
        if smoothed_range > 0:
            result_plus[index] = 100.0 * smoothed_plus / smoothed_range
            result_minus[index] = 100.0 * smoothed_minus / smoothed_range
    return tuple(result_plus), tuple(result_minus)


def fallback_vwap(bars: tuple[Candle, ...]) -> tuple[float | None, ...]:
    """Session VWAP with equal-weight typical price when HistData has no volume."""
    result: list[float | None] = []
    day: object = None
    weighted = 0.0
    volume = 0.0
    count = 0
    for bar in bars:
        if bar.ts_utc.date() != day:
            day = bar.ts_utc.date()
            weighted = volume = 0.0
            count = 0
        typical = (bar.high + bar.low + bar.close) / 3.0
        if bar.volume > 0:
            weighted += typical * bar.volume
            volume += bar.volume
        else:
            weighted += typical
            count += 1
        result.append(weighted / volume if volume > 0 else (weighted / count if count else None))
    return tuple(result)


def signal_directions(chart: tuple[Candle, ...], params: TaiParams, timeframe: int) -> tuple[str | None, ...]:
    """Map closed higher-timeframe Heikin Ashi crosses onto chart bars."""
    signal_tf = params.signal_multiplier * timeframe
    # Chart bars are already aligned. Aggregate complete groups directly rather
    # than re-bucketing multi-minute candles as if they were one-minute input.
    higher_list: list[Candle] = []
    for start in range(0, len(chart) - params.signal_multiplier + 1, params.signal_multiplier):
        group = chart[start : start + params.signal_multiplier]
        if len(group) != params.signal_multiplier:
            continue
        expected = timedelta(minutes=timeframe)
        if any(current.ts_utc - previous.ts_utc != expected for previous, current in zip(group[:-1], group[1:], strict=True)):
            continue
        higher_list.append(Candle(group[0].ts_utc, group[0].open, max(item.high for item in group), min(item.low for item in group), group[-1].close, sum(item.volume for item in group), group[0].series_key, True, sum(item.n_minutes for item in group)))
    higher = tuple(higher_list)
    ha = heikin_ashi(higher)
    crosses: list[tuple[datetime, str]] = []
    for previous, current in zip(ha[:-1], ha[1:], strict=True):
        if previous.close <= previous.open and current.close > current.open:
            crosses.append((current.ts_utc + timedelta(minutes=signal_tf), "long"))
        elif previous.close >= previous.open and current.close < current.open:
            crosses.append((current.ts_utc + timedelta(minutes=signal_tf), "short"))
    result: list[str | None] = [None] * len(chart)
    cursor = 0
    for index, bar in enumerate(chart):
        while cursor < len(crosses) and crosses[cursor][0] <= bar.ts_utc:
            result[index] = crosses[cursor][1]
            cursor += 1
    return tuple(result)


def build_features(minute_candles: tuple[Candle, ...], timeframe: int, params: TaiParams) -> Features:
    chart = tuple(bar for bar in resample_ohlc(minute_candles, timeframe) if bar.complete)
    atr_values = indicators.atr(chart, params.atr_period)
    ema_values = indicators.ema([bar.close for bar in chart], params.ema_period)
    adx_values = indicators.adx(chart, params.adx_period)
    plus_di, minus_di = directional_index(chart, params.adx_period)
    return Features(chart, atr_values, ema_values, adx_values, plus_di, minus_di, fallback_vwap(chart), signal_directions(chart, params, timeframe))


def _passes(features: Features, index: int, direction: str, params: TaiParams, *, disabled: frozenset[str] = frozenset()) -> bool:
    bar = features.bars[index]
    atr_value = features.atr[index]
    if atr_value is None or atr_value <= 0:
        return False
    ema = features.ema[index]
    adx = features.adx[index]
    plus = features.plus_di[index]
    minus = features.minus_di[index]
    vwap = features.vwap[index]
    if "ema" not in disabled and (ema is None or (bar.close <= ema if direction == "long" else bar.close >= ema)):
        return False
    if "adx" not in disabled and (adx is None or adx < params.adx_min or plus is None or minus is None or (plus <= minus if direction == "long" else minus <= plus)):
        return False
    if vwap is None:
        return False
    if "vwap_direction" not in disabled and (bar.close <= vwap if direction == "long" else bar.close >= vwap):
        return False
    if "dont_chase" not in disabled and abs(bar.close - vwap) / atr_value > params.vwap_distance_atr:
        return False
    return True


def _risk(features: Features, index: int, direction: str, params: TaiParams, *, disabled: frozenset[str] = frozenset()) -> tuple[float, float] | None:
    atr_value = features.atr[index]
    if atr_value is None or atr_value <= 0:
        return None
    bar = features.bars[index]
    if "swing_stop" in disabled:
        stop = bar.close - atr_value if direction == "long" else bar.close + atr_value
    elif index < params.swing_lookback:
        return None
    elif direction == "long":
        stop = min(item.low for item in features.bars[index - params.swing_lookback : index]) - params.swing_buffer_atr * atr_value
    else:
        stop = max(item.high for item in features.bars[index - params.swing_lookback : index]) + params.swing_buffer_atr * atr_value
    risk = bar.close - stop if direction == "long" else stop - bar.close
    if risk <= 0 or risk > params.max_risk_atr * atr_value:
        return None
    return stop, risk


def _real_r(level: int, stopped: bool) -> float:
    if stopped:
        return {-1: -1.0, 0: -1.0, 1: 1.0 / 3.0, 2: 1.0}[level]
    return {1: 1.0 / 3.0, 2: 1.0, 3: 2.0}[level]


def _dashboard_r(level: int, stopped: bool) -> float:
    if stopped:
        return -1.0
    return float(level)


def simulate(features: Features, params: TaiParams, *, disabled: frozenset[str] = frozenset(), mirror: bool = False) -> tuple[Trade, ...]:
    trades: list[Trade] = []
    open_trade: dict[str, Any] | None = None
    last_exit = -10_000
    for index, bar in enumerate(features.bars):
        if open_trade is not None:
            direction = open_trade["direction"]
            risk = open_trade["risk"]
            level = open_trade["level"]
            stop = open_trade["stop"]
            stop_hit = bar.low <= stop if direction == "long" else bar.high >= stop
            target3 = float(open_trade["target_multiple"])
            reached = [target for target in (1.0, 2.0, target3) if (bar.high >= open_trade["entry"] + target * risk if direction == "long" else bar.low <= open_trade["entry"] - target * risk)]
            # Conservative intrabar ordering: stop is evaluated before any target.
            if stop_hit:
                real = _real_r(level, True)
                dashboard = _dashboard_r(level, True)
                reason = "stop"
            elif reached:
                level = max(reached)
                if level >= target3:
                    real = _real_r(3, False)
                    dashboard = _dashboard_r(3, False)
                    reason = "tp3"
                else:
                    open_trade["level"] = level
                    if level == 1:
                        open_trade["stop"] = open_trade["entry"]
                    continue
            else:
                continue
            entry = open_trade["entry"]
            costs = open_trade["cost"]
            real_net = real * risk - costs
            dash_net = dashboard * risk - costs
            single: dict[str, float] = {}
            for target in ("1R", "2R", "3R"):
                multiple = int(target[0])
                single[target] = (float(multiple) if level >= multiple else -1.0 if reason == "stop" else 0.0) * risk - costs
            trades.append(Trade(direction, open_trade["index"], open_trade["time"], entry, risk, real_net / risk, dash_net / risk, {key: value / risk for key, value in single.items()}, index, bar.ts_utc, reason))
            open_trade = None
            last_exit = index
            continue
        direction = features.signals[index]
        if direction is None and last_exit >= 0 and "refresh" not in disabled and index - last_exit >= params.refresh_bars:
            direction = "long" if _passes(features, index, "long", params, disabled=disabled) else "short" if _passes(features, index, "short", params, disabled=disabled) else None
        if direction is None:
            continue
        if mirror:
            direction = "short" if direction == "long" else "long"
        if not _passes(features, index, direction, params, disabled=disabled):
            continue
        risk_data = _risk(features, index, direction, params, disabled=disabled)
        if risk_data is None:
            continue
        stop, risk = risk_data
        cost = 0.0
        open_trade = {"direction": direction, "index": index, "time": bar.ts_utc, "entry": bar.close, "risk": risk, "stop": stop, "level": 0, "cost": cost, "target_multiple": params.target_multiple}
    return tuple(trades)


def summarize(trades: Iterable[Trade], start: datetime, end: datetime, cost_points: float) -> dict[str, Any]:
    selected = [trade for trade in trades if start <= trade.entry_time < end]
    # Trade R values are stored before costs; reapply the explicit cost per round trip.
    def stats(field: str, multiple: str | None = None) -> dict[str, Any]:
        values = [float(trade.single_r[multiple]) * trade.risk_points - cost_points if multiple else float(getattr(trade, field)) * trade.risk_points - cost_points for trade in selected]
        r_values = [float(trade.single_r[multiple]) - cost_points / trade.risk_points if multiple else float(getattr(trade, field)) - cost_points / trade.risk_points for trade in selected]
        gains = sum(value for value in values if value > 0)
        losses = -sum(value for value in values if value < 0)
        equity = 0.0
        peak = 0.0
        drawdown = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        return {"trades": len(values), "wins": sum(value > 0 for value in values), "win_rate": sum(value > 0 for value in values) / len(values) if values else 0.0, "net_points": sum(values), "total_r": sum(r_values), "profit_factor": gains / losses if losses else ("inf" if gains else 0.0), "max_drawdown_points": drawdown, "average_r": sum(r_values) / len(r_values) if r_values else 0.0}
    return {"real": stats("real_r"), "dashboard": stats("dashboard_r"), "single_exit": {key: stats("real_r", key) for key in ("1R", "2R", "3R")}}


def variant_params(base: TaiParams) -> tuple[TaiParams, ...]:
    """Bounded one-factor-at-a-time tuning; trial total is explicit and reproducible."""
    candidates = [base]
    for field, values in (("signal_multiplier", (12, 24)), ("adx_min", (15.0, 25.0)), ("vwap_distance_atr", (1.0, 2.0)), ("swing_lookback", (3, 8)), ("swing_buffer_atr", (0.1, 0.3)), ("max_risk_atr", (1.5, 2.5)), ("target_multiple", (2.0, 4.0))):
        for value in values:
            candidates.append(replace(base, **{field: value}))
    return tuple(candidates)


def run_group(minute_candles: tuple[Candle, ...], instrument: str, timeframe: int, normal_cost: float) -> dict[str, Any]:
    base = TaiParams()
    features = build_features(minute_candles, timeframe, base)
    baseline = simulate(features, base)
    mirror = simulate(features, base, mirror=True)
    ablations: dict[str, Any] = {}
    for name in ("ema", "adx", "vwap_direction", "dont_chase", "swing_stop", "refresh"):
        ablations[name] = summarize(simulate(features, base, disabled=frozenset({name})), PERIODS["2022"][0], END_HOLDOUT, normal_cost)
    variants: list[dict[str, Any]] = []
    for params in variant_params(base):
        variant_features = build_features(minute_candles, timeframe, params)
        variant_trades = simulate(variant_features, params)
        oos = summarize(variant_trades, PERIODS["2025"][0], END_HOLDOUT, normal_cost)
        variants.append({"parameters": params.__dict__ if hasattr(params, "__dict__") else {field: getattr(params, field) for field in params.__slots__}, "oos": oos["real"], "trades": len(variant_trades)})
    variants.sort(key=lambda row: float(row["oos"]["net_points"]), reverse=True)
    periods = {name: summarize(baseline, start, end, normal_cost) for name, (start, end) in PERIODS.items()}
    mirror_periods = {name: summarize(mirror, start, end, normal_cost) for name, (start, end) in PERIODS.items()}
    return {"instrument": instrument, "timeframe": timeframe, "bar_count": len(features.bars), "baseline": periods, "mirror": mirror_periods, "ablations": ablations, "variants": {"trial_total": len(variants), "best_oos_normal_cost": variants[0] if variants else None}, "example_equity": [round(sum(trade.real_r for trade in baseline[:index + 1]), 4) for index in range(min(len(baseline), 120))]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--memory-limit-gb", type=float, default=13.0)
    args = parser.parse_args()
    payload: dict[str, Any] = {"task": "T-147", "generated_at": stamp(), "diagnostic_only": True, "sealed_holdout_read": False, "gate1_evaluated": False, "broker_calls": 0, "orders": 0, "preflight": {"cpu_count": os.cpu_count(), "rss_start_bytes": rss_bytes(), "memory_limit_gb": args.memory_limit_gb}, "groups": {}}
    for instrument in INSTRUMENTS:
        for timeframe in TIMEFRAMES:
            group = f"{instrument}:{timeframe}"
            candles = load_training_window(args.lake, instrument, timeframe)
            payload["groups"][group] = run_group(candles, instrument, timeframe, 4.0 if instrument == "NSXUSD" else 1.6)
            if rss_bytes() and rss_bytes() > args.memory_limit_gb * 1024**3:
                raise RuntimeError("RSS exceeded the registered 13 GB cap")
            print(f"completed {group} bars={payload['groups'][group]['bar_count']} rss={rss_bytes()}", flush=True)
        gc.collect()
    payload["preflight"]["rss_end_bytes"] = rss_bytes()
    write_json(args.output, payload)
    lines = ["# T-147 Tai Trading Smart Filter diagnostic", "", "Diagnostic only: the sealed holdout, Gate 1, broker, paper execution and trial ledger were not accessed.", "", f"Generated `{payload['generated_at']}`. Signal timeframe is chart timeframe × 18; entries use closed bars and stop-first intrabar ordering.", "", "| Group | Year | Trades | Win rate | Real net normal | Dashboard net normal | Real total R | Dashboard total R |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for group, data in payload["groups"].items():
        for period, result in data["baseline"].items():
            real = result["real"]; dashboard = result["dashboard"]
            lines.append(f"| {group} | {period} | {real['trades']} | {real['win_rate']:.3f} | {real['net_points']:.3f} | {dashboard['net_points']:.3f} | {real['total_r']:.3f} | {dashboard['total_r']:.3f} |")
    lines += ["", "## Ablation and variant notes", "", "Each group includes one-factor-at-a-time ablations for EMA, ADX, VWAP direction, the don't-chase distance, swing stop, refresh entries, and the scale-out/single-exit accounting. Variant trials are counted explicitly and the best result is selected by 2025-to-holdout normal-cost net points only.", "", "All values are diagnostic evidence; no candidate is a Gate 1 pass or promotion recommendation."]
    args.table.parent.mkdir(parents=True, exist_ok=True)
    args.table.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
