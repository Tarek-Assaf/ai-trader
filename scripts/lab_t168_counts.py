#!/usr/bin/env python3
"""Replay T-162 with full warm-up, then filter dashboard windows."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import lab_t162_pine_port as port

NY = ZoneInfo("America/New_York")
WINDOWS = {
    "ny_midnight_13_15": (datetime(2026, 10, 13, tzinfo=NY), datetime(2026, 10, 15, tzinfo=NY)),
    "utc_midnight_13_15": (datetime(2026, 10, 13, tzinfo=UTC), datetime(2026, 10, 15, tzinfo=UTC)),
    "owner_trade_window": (datetime(2026, 10, 6, tzinfo=NY), datetime(2026, 10, 17, tzinfo=NY)),
    "nq1_reference_window": (datetime(2026, 10, 6, tzinfo=NY), datetime(2026, 10, 17, tzinfo=NY)),
}
REFERENCE = {"cf": {"trades": 53, "sl": 12, "tp1": 15, "tp2": 9, "tp3": 17}, "nq1": {"trades": 115, "sl": 38, "tp2": 12, "tp3": 32}}


def summarize(trades: list[port.PortTrade], days: float = 1.0) -> dict[str, object]:
    counts = Counter(t.exit_reason for t in trades if t.exit_reason)
    completed = sum(counts.values())
    total_r = sum({"stop": -1.0, "be": 0.0, "tp1": 1.0, "tp2": 2.0, "tp3": 3.0}.get(t.exit_reason, 0.0) for t in trades)
    return {
        "trades": len(trades), "completed": completed, "open_at_end": len(trades) - completed,
        "sl": counts.get("stop", 0), "tp1": counts.get("tp1", 0), "tp2": counts.get("tp2", 0),
        "tp3": counts.get("tp3", 0), "be": counts.get("be", 0),
        "win_rate_completed_excluding_be": (counts.get("tp1", 0) + counts.get("tp2", 0) + counts.get("tp3", 0)) / completed if completed else 0.0,
        "total_r": total_r, "mix_denominator": completed,
        "trades_per_day": len(trades) / days if days else 0.0,
    }


def condition_diagnostics(bars, features, mode: str) -> dict[str, object]:
    counts: dict[str, int] = defaultdict(int)
    for i, bar in enumerate(bars):
        for direction, cross in (("long", features.buy_cross[i]), ("short", features.sell_cross[i])):
            if cross:
                counts["direction"] += 1
                ema, adx, plus, minus, vwap, atr = (features.ema[i], features.adx[i], features.plus_di[i], features.minus_di[i], features.vwap[i], features.atr[i])
                ema_ok = ema is not None and ((bar.close > ema) if direction == "long" else (bar.close < ema))
                adx_ok = None not in (adx, plus, minus) and float(adx) >= 20 and ((float(plus) > float(minus)) if direction == "long" else (float(minus) > float(plus)))
                vwap_ok = None not in (vwap, atr) and ((bar.close > vwap) if direction == "long" else (bar.close < vwap)) and abs(bar.close - float(vwap)) / float(atr) <= port.VWAP_DISTANCE_ATR
                if ema_ok: counts["ema"] += 1
                if adx_ok: counts["adx_di"] += 1
                if vwap_ok: counts["vwap"] += 1
                if ema_ok and adx_ok and vwap_ok: counts["smart_ok"] += 1
                if port._risk(bars, features, i, direction)[0]: counts["risk"] += 1
    return {"mode": mode, "bars": len(bars), "condition_pass_counts": dict(counts), "no_chase": {"implemented": False, "pass_count": None, "note": "The unchanged T-162 port has no separate no-chase predicate."}, "refresh": {"implemented": True, "note": "Refresh entries are evaluated inside replay after the exit cooldown."}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", type=Path, required=True)
    ap.add_argument("--owner-file", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    owners = port.read_owner_entries(args.owner_file)
    warm_start = min(o.entry_time for o in owners) - timedelta(days=7)
    end = datetime.combine(datetime(2026, 10, 17), time.min, tzinfo=NY).astimezone(UTC)
    minutes = port.read_nas100_minutes(args.lake, warm_start, end)
    bars = tuple(item for item in port.resample_ohlc(minutes, port.TIMEFRAME) if item.complete)
    if not bars: raise RuntimeError("NAS100 3m data is missing")
    modes: dict[str, object] = {}
    for mode in ("closed", "forming"):
        trades, features = port.replay(bars, mode)
        filtered = {}
        for name, (start, stop) in WINDOWS.items():
            lo, hi = start.astimezone(UTC), stop.astimezone(UTC)
            filtered[name] = {"start": lo.isoformat(), "end": hi.isoformat(), "summary": summarize([t for t in trades if lo <= t.entry_time < hi], (hi - lo).total_seconds() / 86400.0)}
        daily: dict[str, dict[str, object]] = {}
        by_day: dict[str, list[port.PortTrade]] = defaultdict(list)
        for trade in trades:
            by_day[trade.entry_time.astimezone(NY).date().isoformat()].append(trade)
        if bars:
            first_day = bars[0].ts_utc.astimezone(NY).date()
            last_day = bars[-1].ts_utc.astimezone(NY).date()
            cursor = first_day
            while cursor <= last_day:
                key = cursor.isoformat()
                daily[key] = summarize(by_day.get(key, []))
                cursor += timedelta(days=1)
        modes[mode] = {"full_range_trades": len(trades), "windows": filtered, "per_ny_day": daily, "forming_condition_diagnostics": condition_diagnostics(bars, features, mode)}
    payload = {"task": "T-168", "generated_at": datetime.now(UTC).isoformat(), "warmup_start": warm_start.astimezone(UTC).isoformat(), "end": end.isoformat(), "owner_timezone": "America/New_York", "bars": len(bars), "reference": REFERENCE, "modes": modes, "safety": {"broker_calls": 0, "orders": 0, "sealed_holdout_read": False, "lake_writes": 0, "production_state_writes": 0, "tuning": False}}
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    closed = modes["closed"]["windows"]
    lines = ["# T-168 warm-up-preserving dashboard count rerun", "", "**Verdict:** Full-lake warm-up results are reported below. The unchanged port is filtered by entry time after replay; no strategy or port code was changed.", "", "| Window | Trades | SL | TP1 | TP2 | TP3 | BE | Win rate (completed) | Total R | Trades/day |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, item in closed.items():
        s = item["summary"]; lines.append(f"| {name} | {s['trades']} | {s['sl']} | {s['tp1']} | {s['tp2']} | {s['tp3']} | {s['be']} | {s['win_rate_completed_excluding_be']:.2%} | {s['total_r']:.2f} | {s['trades_per_day']:.2f} |")
    lines += ["", "## Reference dashboards", "", "- CFD dashboard (Q-068): 53 trades, 12 SL, 15 TP1, 9 TP2, 17 TP3, 77.36% win rate.", "- NQ1 dashboard (Q-067): 115 trades, 38 SL, 12 TP2, 32 TP3; TP1 was hidden and is not guessed.", "", "## Forming-mode diagnostics", "", json.dumps(modes["forming"]["forming_condition_diagnostics"], indent=2), "", "Safety counters are all zero/false; no broker, order, holdout, lake-write, production-state, tuning or strategy-search activity."]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__": main()
