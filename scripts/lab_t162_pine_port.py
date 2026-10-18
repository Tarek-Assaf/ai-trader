#!/usr/bin/env python3
"""Faithful, diagnostic-only Python port of the owner Tai Smart Filter Pine source.

The port follows the stored Pine source line-by-line for Open/Close setup: forming or
closed 54-minute Heikin-Ashi signals, refresh entries without an HA gate, chart-bar
indicators, entry-bar management, and target-first intrabar ordering. It never calls a
broker, reads the sealed holdout, tunes parameters, or writes the lake.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

try:
    from trader.data.resample import Candle, resample_ohlc
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from trader.data.resample import Candle, resample_ohlc

try:
    from scripts.lab_t151_owner_trades import read_nas100_minutes, read_owner_entries
    from scripts.lab_t160_owner_time_ny import parse_owner_datetime
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.lab_t151_owner_trades import read_nas100_minutes, read_owner_entries
    from scripts.lab_t160_owner_time_ny import parse_owner_datetime

TIMEFRAME = 3
TF_MULT = 18
HIGHER_MINUTES = 54
PERIOD = 14
EMA_PERIOD = 200
SWING_LOOKBACK = 5
REFRESH_BARS = 12
MAX_RISK_ATR = 2.0
VWAP_DISTANCE_ATR = 1.5
START_DATE = datetime(2023, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class OwnerExit:
    trade_number: str
    direction: str
    entry_time: datetime
    exit_time: datetime
    exit_reason: str


@dataclass(frozen=True, slots=True)
class PortTrade:
    direction: str
    entry_index: int
    entry_time: datetime
    entry_price: float
    stop_price: float
    exit_index: int | None
    exit_time: datetime | None
    exit_reason: str | None
    highest_target: int


@dataclass(frozen=True, slots=True)
class Features:
    ema: tuple[float | None, ...]
    atr: tuple[float | None, ...]
    adx: tuple[float | None, ...]
    plus_di: tuple[float | None, ...]
    minus_di: tuple[float | None, ...]
    vwap: tuple[float | None, ...]
    ha_open: tuple[float | None, ...]
    ha_close: tuple[float | None, ...]
    buy_cross: tuple[bool, ...]
    sell_cross: tuple[bool, ...]


def _rma(values: Sequence[float | None], period: int) -> tuple[float | None, ...]:
    out: list[float | None] = [None] * len(values)
    valid = [i for i, value in enumerate(values) if value is not None]
    if not valid:
        return tuple(out)
    first = valid[0]
    if len(values) - first < period:
        return tuple(out)
    window = [float(values[i]) for i in range(first, first + period)]
    current = math.fsum(window) / period
    index = first + period - 1
    out[index] = current
    for index in range(index + 1, len(values)):
        value = values[index]
        if value is not None:
            current = (current * (period - 1) + float(value)) / period
            out[index] = current
    return tuple(out)


def _ema(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(out)
    current = math.fsum(values[:period]) / period
    out[period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        current += alpha * (values[index] - current)
        out[index] = current
    return tuple(out)


def _true_range(bars: Sequence[Candle]) -> tuple[float, ...]:
    result: list[float] = []
    previous: float | None = None
    for bar in bars:
        value = bar.high - bar.low
        if previous is not None:
            value = max(value, abs(bar.high - previous), abs(bar.low - previous))
        result.append(value)
        previous = bar.close
    return tuple(result)


def _atr(bars: Sequence[Candle], period: int = PERIOD) -> tuple[float | None, ...]:
    return _rma(_true_range(bars), period)


def _dmi(bars: Sequence[Candle], period: int = PERIOD) -> tuple[tuple[float | None, ...], ...]:
    tr = _true_range(bars)
    plus: list[float] = [0.0]
    minus: list[float] = [0.0]
    for previous, current in zip(bars[:-1], bars[1:], strict=True):
        up = current.high - previous.high
        down = previous.low - current.low
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    smooth_tr = _rma(tr, period)
    smooth_plus = _rma(plus, period)
    smooth_minus = _rma(minus, period)
    plus_di: list[float | None] = [None] * len(bars)
    minus_di: list[float | None] = [None] * len(bars)
    dx: list[float | None] = [None] * len(bars)
    for i in range(len(bars)):
        if smooth_tr[i] is None or smooth_plus[i] is None or smooth_minus[i] is None:
            continue
        if smooth_tr[i] <= 0:
            plus_di[i] = minus_di[i] = dx[i] = 0.0
            continue
        p = 100.0 * float(smooth_plus[i]) / float(smooth_tr[i])
        m = 100.0 * float(smooth_minus[i]) / float(smooth_tr[i])
        plus_di[i], minus_di[i] = p, m
        dx[i] = 0.0 if p + m <= 0 else 100.0 * abs(p - m) / (p + m)
    adx = _rma(dx, period)
    return tuple(adx), tuple(plus_di), tuple(minus_di)


def _vwap(bars: Sequence[Candle]) -> tuple[float | None, ...]:
    result: list[float | None] = []
    current_day: object = None
    weighted = 0.0
    volume = 0.0
    equal_sum = 0.0
    equal_count = 0
    for bar in bars:
        if bar.ts_utc.date() != current_day:
            current_day = bar.ts_utc.date()
            weighted = volume = equal_sum = 0.0
            equal_count = 0
        typical = (bar.high + bar.low + bar.close) / 3.0
        if bar.volume > 0:
            weighted += typical * bar.volume
            volume += bar.volume
        else:
            equal_sum += typical
            equal_count += 1
        result.append((weighted / volume) if volume > 0 else (equal_sum / equal_count if equal_count else None))
    return tuple(result)


def _ha_complete(bars: Sequence[Candle]) -> tuple[dict[int, tuple[float, float]], ...]:
    """Return complete 54m HA values keyed by chart-bar index within each group."""
    groups: dict[int, list[int]] = defaultdict(list)
    for i, bar in enumerate(bars):
        epoch = int(bar.ts_utc.timestamp() // 60)
        groups[epoch - epoch % HIGHER_MINUTES].append(i)
    result: list[dict[int, tuple[float, float]]] = [dict() for _ in bars]
    previous: tuple[float, float] | None = None
    for _, indices in sorted(groups.items()):
        if len(indices) != HIGHER_MINUTES // TIMEFRAME:
            continue
        first, last = indices[0], indices[-1]
        if any(bars[a].ts_utc + timedelta(minutes=TIMEFRAME) != bars[b].ts_utc for a, b in zip(indices[:-1], indices[1:], strict=True)):
            continue
        raw_open = bars[first].open
        raw_close = bars[last].close
        close = (raw_open + max(bars[i].high for i in indices) + min(bars[i].low for i in indices) + raw_close) / 4.0
        open_price = (raw_open + raw_close) / 2.0 if previous is None else (previous[0] + previous[1]) / 2.0
        previous = (open_price, close)
        for i in indices:
            result[i][0] = (open_price, close)
    return tuple(result)


def _ha_series(bars: Sequence[Candle], mode: str) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    if mode not in {"closed", "forming"}:
        raise ValueError("mode must be closed or forming")
    groups: dict[int, list[int]] = defaultdict(list)
    for i, bar in enumerate(bars):
        epoch = int(bar.ts_utc.timestamp() // 60)
        groups[epoch - epoch % HIGHER_MINUTES].append(i)
    ordered = sorted(groups.items())
    complete: list[tuple[int, list[int], tuple[float, float]]] = []
    previous: tuple[float, float] | None = None
    for bucket, indices in ordered:
        if len(indices) != HIGHER_MINUTES // TIMEFRAME:
            continue
        if any(bars[a].ts_utc + timedelta(minutes=TIMEFRAME) != bars[b].ts_utc for a, b in zip(indices[:-1], indices[1:], strict=True)):
            continue
        raw_open = bars[indices[0]].open
        raw_close = bars[indices[-1]].close
        close = (raw_open + max(bars[i].high for i in indices) + min(bars[i].low for i in indices) + raw_close) / 4.0
        open_price = (raw_open + raw_close) / 2.0 if previous is None else (previous[0] + previous[1]) / 2.0
        previous = (open_price, close)
        complete.append((bucket, indices, (open_price, close)))
    opens: list[float | None] = [None] * len(bars)
    closes: list[float | None] = [None] * len(bars)
    complete_by_bucket = {bucket: (indices, value) for bucket, indices, value in complete}
    previous_complete: tuple[float, float] | None = None
    for bucket, indices in ordered:
        if bucket in complete_by_bucket:
            full_indices, value = complete_by_bucket[bucket]
            if mode == "closed":
                # lookahead_off exposes the last confirmed HTF value through the current group.
                for i in indices:
                    if previous_complete is not None:
                        opens[i], closes[i] = previous_complete
                previous_complete = value
            else:
                for i in full_indices:
                    prefix = [j for j in indices if j <= i]
                    raw_open = bars[prefix[0]].open
                    raw_close = bars[prefix[-1]].close
                    close = (raw_open + max(bars[j].high for j in prefix) + min(bars[j].low for j in prefix) + raw_close) / 4.0
                    open_price = (raw_open + raw_close) / 2.0 if previous_complete is None else (previous_complete[0] + previous_complete[1]) / 2.0
                    opens[i], closes[i] = open_price, close
                previous_complete = value
        elif mode == "forming":
            if not indices:
                continue
            for i in indices:
                prefix = [j for j in indices if j <= i]
                raw_open = bars[prefix[0]].open
                raw_close = bars[prefix[-1]].close
                close = (raw_open + max(bars[j].high for j in prefix) + min(bars[j].low for j in prefix) + raw_close) / 4.0
                open_price = (raw_open + raw_close) / 2.0 if previous_complete is None else (previous_complete[0] + previous_complete[1]) / 2.0
                opens[i], closes[i] = open_price, close
    return tuple(opens), tuple(closes)


def _crosses(opens: Sequence[float | None], closes: Sequence[float | None]) -> tuple[tuple[bool, ...], tuple[bool, ...]]:
    buy: list[bool] = [False] * len(opens)
    sell: list[bool] = [False] * len(opens)
    for i in range(1, len(opens)):
        po, pc, co, cc = opens[i - 1], closes[i - 1], opens[i], closes[i]
        if None in (po, pc, co, cc):
            continue
        buy[i] = bool(pc <= po and cc > co)
        sell[i] = bool(pc >= po and cc < co)
    return tuple(buy), tuple(sell)


def compute_features(bars: Sequence[Candle], mode: str) -> Features:
    ema = _ema([b.close for b in bars], EMA_PERIOD)
    atr = _atr(bars)
    adx, plus, minus = _dmi(bars)
    vwap = _vwap(bars)
    ha_open, ha_close = _ha_series(bars, mode)
    buy, sell = _crosses(ha_open, ha_close)
    return Features(ema, atr, adx, plus, minus, vwap, ha_open, ha_close, buy, sell)


def _risk(bars: Sequence[Candle], features: Features, i: int, direction: str) -> tuple[bool, float]:
    atr = features.atr[i]
    if atr is None or atr <= 0 or i < SWING_LOOKBACK:
        return False, 0.0
    previous = bars[i - SWING_LOOKBACK : i]
    stop = min(b.low for b in previous) - float(atr) * 0.20 if direction == "long" else max(b.high for b in previous) + float(atr) * 0.20
    risk = bars[i].close - stop if direction == "long" else stop - bars[i].close
    return risk > 0 and risk / float(atr) <= MAX_RISK_ATR, risk


def _smart_ok(bars: Sequence[Candle], features: Features, i: int, direction: str) -> bool:
    ema, atr, adx, plus, minus, vwap = (features.ema[i], features.atr[i], features.adx[i], features.plus_di[i], features.minus_di[i], features.vwap[i])
    if None in (ema, atr, adx, plus, minus, vwap):
        return False
    close = bars[i].close
    if direction == "long":
        trend = close > float(ema)
        directional = float(adx) >= 20 and float(plus) > float(minus)
        side = close > float(vwap)
    else:
        trend = close < float(ema)
        directional = float(adx) >= 20 and float(minus) > float(plus)
        side = close < float(vwap)
    location = abs(close - float(vwap)) / float(atr) <= VWAP_DISTANCE_ATR
    return trend and directional and side and location


def replay(bars: Sequence[Candle], mode: str) -> tuple[tuple[PortTrade, ...], Features]:
    features = compute_features(bars, mode)
    trades: list[PortTrade] = []
    active: dict[str, Any] | None = None
    last_exit: int | None = None
    for i, bar in enumerate(bars):
        prev_in_trade = active is not None
        signals: list[str] = []
        for direction, cross in (("long", features.buy_cross[i]), ("short", features.sell_cross[i])):
            if not cross or not _smart_ok(bars, features, i, direction):
                continue
            risk_ok, _ = _risk(bars, features, i, direction)
            if risk_ok:
                signals.append(direction)
        if last_exit is not None and i - last_exit >= REFRESH_BARS:
            for direction in ("long", "short"):
                if _smart_ok(bars, features, i, direction):
                    risk_ok, _ = _risk(bars, features, i, direction)
                    if risk_ok:
                        signals.append(direction)
        if active is None and signals:
            direction = signals[0]
            _, risk = _risk(bars, features, i, direction)
            stop = bar.close - risk if direction == "long" else bar.close + risk
            active = {"direction": direction, "entry_index": i, "entry_time": bar.ts_utc, "entry_price": bar.close, "stop_price": stop, "risk": risk, "highest": 0, "active_stop": stop}
        if active is not None:
            direction = active["direction"]
            long = direction == "long"
            hit_stop = bar.low <= active["active_stop"] if long else bar.high >= active["active_stop"]
            hit1 = bar.high >= active["entry_price"] + active["risk"] if long else bar.low <= active["entry_price"] - active["risk"]
            hit2 = bar.high >= active["entry_price"] + active["risk"] * 2 if long else bar.low <= active["entry_price"] - active["risk"] * 2
            hit3 = bar.high >= active["entry_price"] + active["risk"] * 3 if long else bar.low <= active["entry_price"] - active["risk"] * 3
            reason: str | None = None
            if active["highest"] == 0:
                if hit3: active["highest"], reason = 3, "tp3"
                elif hit2: active["highest"], active["active_stop"] = 2, active["entry_price"]
                elif hit1: active["highest"], active["active_stop"] = 1, active["entry_price"]
                elif hit_stop: reason = "stop"
            else:
                if hit3: active["highest"], reason = 3, "tp3"
                elif hit2: active["highest"], active["active_stop"] = 2, active["entry_price"]
                elif hit_stop: reason = "be"
            if reason is not None:
                trades.append(PortTrade(direction, active["entry_index"], active["entry_time"], active["entry_price"], active["stop_price"], i, bar.ts_utc, reason, active["highest"]))
                active = None
        if prev_in_trade and active is None:
            last_exit = i
    if active is not None:
        trades.append(PortTrade(active["direction"], active["entry_index"], active["entry_time"], active["entry_price"], active["stop_price"], None, None, None, active["highest"]))
    return tuple(trades), features


def read_owner_exits(path: Path) -> tuple[OwnerExit, ...]:
    entries: dict[str, tuple[str, datetime]] = {}
    exits: list[OwnerExit] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            number = str(row.get("Trade number", ""))
            kind = str(row.get("Type", "")).lower()
            direction = "long" if "long" in kind else "short" if "short" in kind else ""
            if kind.startswith("entry") and direction:
                entries[number] = (direction, parse_owner_datetime(str(row["Date and time"])))
            elif kind.startswith("exit") and number in entries:
                d, entry_time = entries[number]
                exits.append(OwnerExit(number, d, entry_time, parse_owner_datetime(str(row["Date and time"])), str(row.get("Signal", ""))))
    return tuple(exits)


def _match(entries: Sequence[Any], trades: Sequence[PortTrade], tolerance: int) -> tuple[int, list[dict[str, Any]]]:
    remaining = list(trades)
    matched = 0
    rows: list[dict[str, Any]] = []
    for owner in entries:
        candidates = [t for t in remaining if t.direction == owner.direction]
        if not candidates:
            rows.append({"owner": owner.entry_time.isoformat(), "direction": owner.direction, "match": None})
            continue
        best = min(candidates, key=lambda t: abs((t.entry_time - owner.entry_time).total_seconds()))
        delta = (best.entry_time - owner.entry_time).total_seconds() / 60.0
        if abs(delta) <= tolerance:
            matched += 1
            remaining.remove(best)
            rows.append({"owner": owner.entry_time.isoformat(), "direction": owner.direction, "match": best.entry_time.isoformat(), "delta_minutes": delta})
        else:
            rows.append({"owner": owner.entry_time.isoformat(), "direction": owner.direction, "match": best.entry_time.isoformat(), "delta_minutes": delta, "first_difference": "entry_time_or_signal"})
    return matched, rows


def _json_trade(trade: PortTrade) -> dict[str, Any]:
    return {"direction": trade.direction, "entry_index": trade.entry_index, "entry_time": trade.entry_time.isoformat(), "entry_price": trade.entry_price, "stop_price": trade.stop_price, "exit_index": trade.exit_index, "exit_time": trade.exit_time.isoformat() if trade.exit_time else None, "exit_reason": trade.exit_reason, "highest_target": trade.highest_target}


def run(lake: Path, owner_file: Path, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    owners = read_owner_entries(owner_file)
    if len(owners) != 31:
        raise RuntimeError(f"expected 31 owner entries, found {len(owners)}")
    start = start or min(item.entry_time for item in owners) - timedelta(days=3)
    end = end or max(item.entry_time for item in owners) + timedelta(days=1)
    minutes = read_nas100_minutes(lake, start, end)
    bars = tuple(item for item in resample_ohlc(minutes, TIMEFRAME) if item.complete)
    if not bars:
        raise RuntimeError("NAS100 3m data is missing")
    modes: dict[str, Any] = {}
    for mode in ("closed", "forming"):
        trades, features = replay(bars, mode)
        counts = {}
        differences = {}
        for tolerance in (0, 1, 2):
            counts[str(tolerance)] , differences[str(tolerance)] = _match(owners, trades, tolerance)
        modes[mode] = {"entry_counts": counts, "entry_differences": differences, "trades": [_json_trade(t) for t in trades], "bars": len(bars), "first_feature_values": [{"ts": bars[i].ts_utc.isoformat(), "ema200": features.ema[i], "atr14": features.atr[i], "adx": features.adx[i], "plus_di": features.plus_di[i], "minus_di": features.minus_di[i], "vwap": features.vwap[i]} for i in range(max(0, len(bars)-5), len(bars))]}
    exits = read_owner_exits(owner_file)
    return {"task":"T-162", "generated_at":datetime.now(UTC).isoformat(), "owner_timezone":"America/New_York", "owner_entries":len(owners), "chart_timeframe_minutes":TIMEFRAME, "signal_timeframe_minutes":HIGHER_MINUTES, "modes":modes, "owner_exits":[{"trade_number":e.trade_number,"direction":e.direction,"entry_time":e.entry_time.isoformat(),"exit_time":e.exit_time.isoformat(),"exit_reason":e.exit_reason} for e in exits], "source_contract":{"setup":"Open/Close","refresh_ha_gate":False,"entry_bar_management":True,"intrabar_order":"target-first (TP3, TP2, TP1/BE before stop)","vwap":"volume-weighted when volume is present; equal typical-price weighting for zero-volume recorder bars"}, "safety":{"broker_calls":0,"orders":0,"sealed_holdout_read":False,"lake_writes":0,"production_state_writes":0,"gate_evaluated":False}}


def report(payload: dict[str, Any]) -> str:
    lines=["# T-162 faithful Pine-port diagnostic",""]
    best=max((m["entry_counts"]["2"] for m in payload["modes"].values()), default=0)
    mode=max(payload["modes"], key=lambda k: payload["modes"][k]["entry_counts"]["2"])
    lines.append(f"**Owner verdict:** The faithful Pine port reproduces **{best}/31** entries within ±2 three-minute bars in `{mode}` mode. {'The 25/31 success bar is met.' if best >= 25 else 'The 25/31 success bar is not met; per-entry differences are reported below and the task stops without tuning or a historical rerun.'}")
    lines += ["",f"Generated: `{payload['generated_at']}`. Owner timestamps were parsed as `America/New_York`.","","## Entry reproduction","","| HA mode | Exact | ±1 bar | ±2 bars |","| --- | ---: | ---: | ---: |"]
    for mode,data in payload["modes"].items(): lines.append(f"| {mode} | {data['entry_counts']['0']} | {data['entry_counts']['1']} | {data['entry_counts']['2']} |")
    lines += ["","## Port contract","",json.dumps(payload["source_contract"],indent=2,sort_keys=True),"","## Exit comparison source","",f"Owner exit rows parsed: {len(payload['owner_exits'])}. Port trades include exit time/type where a trade completed; open trades remain explicit.","","## Safety","",json.dumps(payload["safety"],indent=2,sort_keys=True),""]
    if best < 25:
        lines += ["## Per-entry differences",""]
        for mode,data in payload["modes"].items():
            lines += [f"### {mode}",""]
            for row in data["entry_differences"]["2"]:
                if row.get("first_difference"): lines.append(f"- `{row['owner']}` {row['direction']}: nearest port entry `{row.get('match')}` (delta {row.get('delta_minutes'):.1f} minutes).")
    return "\n".join(lines)+"\n"


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--lake",type=Path,required=True)
    parser.add_argument("--owner-file",type=Path,required=True)
    parser.add_argument("--output",type=Path,default=Path("docs/evidence/t162_port.json"))
    parser.add_argument("--report",type=Path,default=Path("docs/evidence/t162_report.md"))
    parser.add_argument("--entries",type=Path,default=Path("docs/evidence/t162_entries.csv"))
    args=parser.parse_args()
    payload=run(args.lake,args.owner_file)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    args.report.write_text(report(payload),encoding="utf-8")
    with args.entries.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.writer(handle); writer.writerow(["mode","owner_entry_time","direction","matched_entry_time","delta_minutes","exit_time","exit_reason"])
        for mode,data in payload["modes"].items():
            for row in data["entry_differences"]["2"]: writer.writerow([mode,row.get("owner"),row.get("direction"),row.get("match"),row.get("delta_minutes"),"",""])
    print(json.dumps({"task":"T-162","modes":{m:d["entry_counts"] for m,d in payload["modes"].items()},"safety":payload["safety"]},sort_keys=True))
    return 0

if __name__ == "__main__": raise SystemExit(main())
