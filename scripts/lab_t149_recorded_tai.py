#!/usr/bin/env python3
"""Run the T-149 Tai Smart Filter diagnostic on recorded Capital.com bars.

The runner is deliberately separate from the promotion pipeline.  It reads the
recorded bid/ask lake, computes indicators on the bid/ask mid price, fills at
the real side of the market, and writes evidence only.  It never calls a
broker, opens an order, reads the sealed holdout, or writes gate/ledger state.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from trader.data.resample import Candle, resample_ohlc

try:
    from scripts.lab_tai_smart_filter import (
        TaiParams,
        _dashboard_r,
        _passes,
        _real_r,
        _risk,
        build_features,
    )
except ModuleNotFoundError:  # direct ``python scripts/lab_t149_recorded_tai.py`` execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.lab_tai_smart_filter import (
        TaiParams,
        _dashboard_r,
        _passes,
        _real_r,
        _risk,
        build_features,
    )

END_HOLDOUT = datetime(2026, 7, 1, tzinfo=UTC)
START = datetime(2026, 10, 5, tzinfo=UTC)
END = datetime(2026, 10, 17, tzinfo=UTC)
INSTRUMENTS = ("NAS100", "XAUUSD")
TIMEFRAMES = (1, 3, 5)


@dataclass(frozen=True, slots=True)
class RecordedMinute:
    ts_utc: datetime
    bid: Candle
    ask: Candle


@dataclass(frozen=True, slots=True)
class RecordedFeatures:
    mid: Any
    bid: tuple[Candle, ...]
    ask: tuple[Candle, ...]


@dataclass(frozen=True, slots=True)
class RecordedTrade:
    direction: str
    entry_time: datetime
    entry_price: float
    stop: float
    targets: tuple[float, float, float]
    exit_time: datetime
    exit_price: float
    exit_reason: str
    risk_points: float
    spread_entry: float
    real_points: float
    dashboard_points: float
    real_r: float
    dashboard_r: float
    single_points: dict[str, float]
    single_r: dict[str, float]
    max_level: int


def stamp() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def rss_bytes() -> int | None:
    try:
        return int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1]) * os.sysconf(
            "SC_PAGE_SIZE"
        )
    except (OSError, ValueError, IndexError):
        return None


def _candle(row: dict[str, Any], symbol: str) -> Candle:
    stamp_value = row["ts_utc"].astimezone(UTC)
    return Candle(
        stamp_value,
        float(row["open"]),
        float(row["high"]),
        float(row["low"]),
        float(row["close"]),
        0.0,
        (symbol, str(row["side"])),
        True,
        1,
    )


def read_recorded_minutes(lake: Path, symbol: str) -> tuple[RecordedMinute, ...]:
    """Read paired bid/ask rows without modifying the recorder lake."""
    rows: dict[tuple[datetime, str], dict[str, Any]] = {}
    month = lake / symbol / "1m_capital" / "2026" / "10.parquet"
    if not month.exists():
        return ()
    for row in pq.read_table(month).to_pylist():
        stamp_value = row["ts_utc"].astimezone(UTC)
        if not START <= stamp_value < END or row.get("side") not in {"bid", "ask"}:
            continue
        rows[(stamp_value, str(row["side"]))] = row
    paired: list[RecordedMinute] = []
    for stamp_value in sorted({key[0] for key in rows}):
        bid = rows.get((stamp_value, "bid"))
        ask = rows.get((stamp_value, "ask"))
        if bid is None or ask is None:
            continue
        paired.append(RecordedMinute(stamp_value, _candle(bid, symbol), _candle(ask, symbol)))
    return tuple(paired)


def mid_candles(minutes: tuple[RecordedMinute, ...], symbol: str) -> tuple[Candle, ...]:
    result: list[Candle] = []
    for item in minutes:
        result.append(
            Candle(
                item.ts_utc,
                (item.bid.open + item.ask.open) / 2.0,
                (item.bid.high + item.ask.high) / 2.0,
                (item.bid.low + item.ask.low) / 2.0,
                (item.bid.close + item.ask.close) / 2.0,
                0.0,
                (symbol, "mid"),
                True,
                1,
            )
        )
    return tuple(result)


def build_recorded_features(
    minutes: tuple[RecordedMinute, ...], symbol: str, timeframe: int
) -> RecordedFeatures:
    mid = build_features(mid_candles(minutes, symbol), timeframe, TaiParams())
    bid = tuple(
        bar for bar in resample_ohlc((item.bid for item in minutes), timeframe) if bar.complete
    )
    ask = tuple(
        bar for bar in resample_ohlc((item.ask for item in minutes), timeframe) if bar.complete
    )
    bid_by_time = {bar.ts_utc: bar for bar in bid}
    ask_by_time = {bar.ts_utc: bar for bar in ask}
    keep = [
        bar.ts_utc for bar in mid.bars if bar.ts_utc in bid_by_time and bar.ts_utc in ask_by_time
    ]
    aligned_mid = tuple(bar for bar in mid.bars if bar.ts_utc in set(keep))
    # Features arrays are indexed with the chart bars; preserve the indicator
    # values already computed by T-147 while dropping incomplete side bars.
    indexes = [index for index, bar in enumerate(mid.bars) if bar.ts_utc in set(keep)]
    aligned = type(mid)(
        aligned_mid,
        tuple(mid.atr[index] for index in indexes),
        tuple(mid.ema[index] for index in indexes),
        tuple(mid.adx[index] for index in indexes),
        tuple(mid.plus_di[index] for index in indexes),
        tuple(mid.minus_di[index] for index in indexes),
        tuple(mid.vwap[index] for index in indexes),
        tuple(mid.signals[index] for index in indexes),
    )
    return RecordedFeatures(
        aligned, tuple(bid_by_time[ts] for ts in keep), tuple(ask_by_time[ts] for ts in keep)
    )


def simulate_recorded(
    features: RecordedFeatures, params: TaiParams | None = None
) -> tuple[RecordedTrade, ...]:
    params = params or TaiParams()
    trades: list[RecordedTrade] = []
    open_trade: dict[str, Any] | None = None
    last_exit = -10_000
    for index, bar in enumerate(features.mid.bars):
        side = (
            features.bid[index]
            if open_trade and open_trade["direction"] == "long"
            else features.ask[index]
        )
        if open_trade is not None:
            direction = open_trade["direction"]
            risk = open_trade["risk"]
            level = open_trade["level"]
            stop = open_trade["stop"]
            stop_hit = side.low <= stop if direction == "long" else side.high >= stop
            reached: list[int] = []
            for target_level, target in enumerate(open_trade["targets"], start=1):
                if side.high >= target if direction == "long" else side.low <= target:
                    reached.append(target_level)
            if stop_hit:
                real_r = _real_r(level, True, params.target_multiple)
                dashboard_r = _dashboard_r(level, True)
                exit_price = stop
                reason = "stop"
            elif reached:
                level = max(reached)
                if level >= 3:
                    real_r = _real_r(3, False, params.target_multiple)
                    dashboard_r = _dashboard_r(3, False)
                    exit_price = open_trade["targets"][2]
                    reason = "tp3"
                else:
                    open_trade["level"] = level
                    if level == 1:
                        open_trade["stop"] = open_trade["entry"]
                    continue
            else:
                continue
            entry = open_trade["entry"]
            real_points = real_r * risk
            dashboard_points = dashboard_r * risk
            single_points = {
                f"{multiple}R": (
                    float(multiple) if level >= multiple else -1.0 if reason == "stop" else 0.0
                )
                * risk
                for multiple in (1, 2, 3)
            }
            trades.append(
                RecordedTrade(
                    direction,
                    open_trade["time"],
                    entry,
                    open_trade["initial_stop"],
                    tuple(open_trade["targets"]),
                    bar.ts_utc,
                    exit_price,
                    reason,
                    risk,
                    open_trade["spread"],
                    real_points,
                    dashboard_points,
                    real_r,
                    dashboard_r,
                    single_points,
                    {key: value / risk for key, value in single_points.items()},
                    level,
                )
            )
            open_trade = None
            last_exit = index
            continue
        direction = features.mid.signals[index]
        if direction is None and last_exit >= 0 and index - last_exit >= params.refresh_bars:
            direction = (
                "long"
                if _passes(features.mid, index, "long", params)
                else "short"
                if _passes(features.mid, index, "short", params)
                else None
            )
        if direction is None or not _passes(features.mid, index, direction, params):
            continue
        risk_data = _risk(features.mid, index, direction, params)
        if risk_data is None:
            continue
        stop, _mid_risk = risk_data
        entry = features.ask[index].close if direction == "long" else features.bid[index].close
        risk = entry - stop if direction == "long" else stop - entry
        if risk <= 0 or risk > params.max_risk_atr * (features.mid.atr[index] or 0):
            continue
        targets = tuple(
            entry + multiple * risk if direction == "long" else entry - multiple * risk
            for multiple in (1.0, 2.0, params.target_multiple)
        )
        open_trade = {
            "direction": direction,
            "time": bar.ts_utc,
            "entry": entry,
            "risk": risk,
            "stop": stop,
            "initial_stop": stop,
            "targets": targets,
            "level": 0,
            "spread": features.ask[index].close - features.bid[index].close,
        }
    return tuple(trades)


def _stats(trades: list[RecordedTrade], field: str = "real_points") -> dict[str, Any]:
    values = [float(getattr(trade, field)) for trade in trades]
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(values),
        "trades_per_day": len(values) / 11.0,
        "win_rate": sum(value > 0 for value in values) / len(values) if values else 0.0,
        "net_points": sum(values),
        "total_r": sum(
            trade.real_r if field == "real_points" else trade.dashboard_r for trade in trades
        ),
        "max_drawdown_points": drawdown,
    }


def summarize(trades: tuple[RecordedTrade, ...]) -> dict[str, Any]:
    selected = list(trades)

    def single_stats(target: str) -> dict[str, Any]:
        values = [trade.single_points[target] for trade in selected]
        equity = peak = drawdown = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        return {
            "trades": len(values),
            "win_rate": sum(value > 0 for value in values) / len(values) if values else 0.0,
            "net_points": sum(values),
            "total_r": sum(trade.single_r[target] for trade in selected),
            "max_drawdown_points": drawdown,
        }

    return {
        "real": _stats(selected, "real_points"),
        "dashboard": _stats(selected, "dashboard_points"),
        "single_exit": {key: single_stats(key) for key in ("1R", "2R", "3R")},
        "positive_real_result": sum(trade.real_points > 0 for trade in selected) / len(selected)
        if selected
        else 0.0,
        "any_target_reached": sum(trade.max_level > 0 for trade in selected) / len(selected)
        if selected
        else 0.0,
    }


def data_check(minutes: tuple[RecordedMinute, ...], symbol: str) -> dict[str, Any]:
    if not minutes:
        return {"symbol": symbol, "rows": 0, "status": "missing"}
    spreads = [item.ask.close - item.bid.close for item in minutes]
    gaps = sum(
        int((later.ts_utc - earlier.ts_utc).total_seconds() // 60 - 1)
        for earlier, later in zip(minutes, minutes[1:], strict=False)
        if timedelta(minutes=1) < later.ts_utc - earlier.ts_utc <= timedelta(minutes=180)
    )
    by_hour: dict[int, list[float]] = defaultdict(list)
    for item, spread in zip(minutes, spreads, strict=True):
        by_hour[item.ts_utc.hour].append(spread)
    hours = {
        str(hour): {
            "count": len(values),
            "median": sorted(values)[len(values) // 2],
            "p90": sorted(values)[max(0, math.ceil(len(values) * 0.9) - 1)],
            "max": max(values),
        }
        for hour, values in sorted(by_hour.items())
    }
    return {
        "symbol": symbol,
        "rows": len(minutes),
        "first_minute_utc": minutes[0].ts_utc,
        "last_minute_utc": minutes[-1].ts_utc,
        "missing_minutes_between_observations": gaps,
        "bid_above_ask_rows": sum(spread < 0 for spread in spreads),
        "zero_spread_rows": sum(spread <= 0 for spread in spreads),
        "spread_points": {
            "median": sorted(spreads)[len(spreads) // 2],
            "p90": sorted(spreads)[max(0, math.ceil(len(spreads) * 0.9) - 1)],
            "max": max(spreads),
        },
        "spread_by_utc_hour": hours,
        "outside_sealed_holdout": minutes[0].ts_utc >= END_HOLDOUT,
    }


def write_trades(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "symbol",
        "timeframe",
        "direction",
        "signal_time_utc",
        "signal_time_riyadh",
        "entry",
        "stop",
        "tp1",
        "tp2",
        "tp3",
        "exit_time",
        "exit_reason",
        "result_points_real",
        "result_r_real",
        "result_points_dashboard",
        "result_r_dashboard",
        "spread_at_entry",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--trades", type=Path, required=True)
    args = parser.parse_args()
    checks: dict[str, Any] = {}
    groups: dict[str, Any] = {}
    trade_rows: list[dict[str, Any]] = []
    for symbol in INSTRUMENTS:
        minutes = read_recorded_minutes(args.lake, symbol)
        checks[symbol] = data_check(minutes, symbol)
        if not minutes or not checks[symbol]["outside_sealed_holdout"]:
            raise RuntimeError(f"recorded {symbol} data is missing or overlaps the sealed holdout")
        for timeframe in TIMEFRAMES:
            key = f"{symbol}:{timeframe}"
            valid_minutes = tuple(item for item in minutes if item.ask.close > item.bid.close)
            features = build_recorded_features(valid_minutes, symbol, timeframe)
            trades = simulate_recorded(features)
            groups[key] = {
                "symbol": symbol,
                "timeframe": timeframe,
                "bars": len(features.mid.bars),
                "summary": summarize(trades),
            }
            for trade in trades:
                trade_rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "direction": trade.direction,
                        "signal_time_utc": trade.entry_time.isoformat(),
                        "signal_time_riyadh": (trade.entry_time + timedelta(hours=3)).isoformat(),
                        "entry": trade.entry_price,
                        "stop": trade.stop,
                        "tp1": trade.targets[0],
                        "tp2": trade.targets[1],
                        "tp3": trade.targets[2],
                        "exit_time": trade.exit_time.isoformat(),
                        "exit_reason": trade.exit_reason,
                        "result_points_real": trade.real_points,
                        "result_r_real": trade.real_r,
                        "result_points_dashboard": trade.dashboard_points,
                        "result_r_dashboard": trade.dashboard_r,
                        "spread_at_entry": trade.spread_entry,
                    }
                )
            print(f"completed {key} bars={len(features.mid.bars)} trades={len(trades)}", flush=True)
    payload = {
        "task": "T-149",
        "generated_at": stamp(),
        "diagnostic_only": True,
        "sealed_holdout_read": False,
        "gate1_evaluated": False,
        "broker_calls": 0,
        "orders": 0,
        "data_check": checks,
        "groups": groups,
        "trade_count": len(trade_rows),
        "timeframes": TIMEFRAMES,
        "symbols": INSTRUMENTS,
        "preflight": {"rss_end_bytes": rss_bytes()},
    }
    write_json(args.output, payload)
    write_trades(args.trades, trade_rows)
    lines = [
        "# T-149 recorded Tai Smart Filter diagnostic",
        "",
        "Diagnostic only on 11 observed October 2026 days. Indicators use bid/ask mid prices; "
        "fills use the real side and recorded spread. 60m is omitted because the window is "
        "too short.",
        "",
        "| Group | Trades | Trades/day | Any target | Positive real result | Real net points | "
        "Real total R | Max DD points |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, data in groups.items():
        stats = data["summary"]["real"]
        lines.append(
            f"| {key} | {stats['trades']} | {stats['trades_per_day']:.3f} | "
            f"{data['summary']['any_target_reached']:.3f} | "
            f"{data['summary']['positive_real_result']:.3f} | {stats['net_points']:.3f} | "
            f"{stats['total_r']:.3f} | {stats['max_drawdown_points']:.3f} |"
        )
    lines += [
        "",
        "No owner trade list was present under docs/owner; no trade matching was invented.",
        "",
        "Safety flags: broker calls 0, orders 0, sealed holdout read false, "
        "Gate 1 evaluated false.",
    ]
    args.table.parent.mkdir(parents=True, exist_ok=True)
    args.table.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
