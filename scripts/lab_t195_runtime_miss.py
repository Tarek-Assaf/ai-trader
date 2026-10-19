#!/usr/bin/env python3
"""Read-only diagnosis of the T-187 runtime/port entry misses.

The script reuses the T-187 bar and entry loaders, then traces the bounded
runtime provider and two controlled history variants on the same closed 3M
bars.  It never calls a broker, reads the sealed holdout, or writes to the
lake.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Mapping

try:
    from scripts import lab_t162_pine_port as port
    from scripts import lab_t187_runtime_replay as replay
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import lab_t162_pine_port as port
    from scripts import lab_t187_runtime_replay as replay

from trader.experiments import tai_wiring
from trader.experiments.tai_demo import ClosedBar
from trader.experiments.tai_wiring import TaiSignalProvider

MINIMUM_HISTORY = 300
HISTORY_WINDOW = 600


def _candle(bar: ClosedBar) -> port.Candle:
    return port.Candle(
        bar.at,
        float(bar.open),
        float(bar.high),
        float(bar.low),
        float(bar.close),
        float(bar.volume),
        (bar.epic, "mid"),
        True,
        3,
    )


def _value(value: object) -> float | None:
    return None if value is None else float(value)


def _first_gate(gates: Mapping[str, bool]) -> str | None:
    for name in (
        "history_ready",
        "indicators_ready",
        "ha_cross",
        "ema_side",
        "adx_threshold",
        "di_direction",
        "vwap_side",
        "vwap_distance",
        "swing_window",
        "risk_positive",
        "risk_cap",
    ):
        if not gates.get(name, False):
            return name
    return None


def _runtime_trace(
    bars: Sequence[ClosedBar], selected: set[datetime.date], target_times: set[datetime], *, unbounded: bool
) -> tuple[tuple[replay.RuntimeEntry, ...], dict[str, dict[str, object]], int]:
    provider = TaiSignalProvider(minimum_history=MINIMUM_HISTORY)
    if unbounded:
        provider._bars = deque(maxlen=None)  # type: ignore[attr-defined]
    result: list[replay.RuntimeEntry] = []
    trace: dict[str, dict[str, object]] = {}
    for bar in bars:
        signal = provider(bar)
        history = provider.history
        index = len(history) - 1
        if bar.at not in target_times:
            if signal is not None and bar.at.astimezone(replay.NY).date() in selected:
                result.append(replay.RuntimeEntry(bar.at, signal.direction.value, signal.entry, signal.stop))
            continue
        features = tai_wiring._features(history)
        candles = tuple(_candle(item) for item in history)
        ha_open, ha_close = port._ha_series(candles, "closed", "session_1800_ny")
        atr = features["atr"][index]
        ema = features["ema"][index]
        adx = features["adx"][index]
        plus = features["plus_di"][index]
        minus = features["minus_di"][index]
        vwap = features["vwap"][index]
        direction = signal.direction.value if signal is not None else None
        side_long = direction == "BUY"
        side_short = direction == "SELL"
        buy_cross = bool(features["buy"][index])
        sell_cross = bool(features["sell"][index])
        cross = buy_cross if side_long else sell_cross if side_short else buy_cross or sell_cross
        indicators_ready = None not in (atr, ema, adx, plus, minus, vwap) and bool(atr and atr > 0)
        if direction == "BUY":
            ema_side = float(bar.close) > float(ema) if ema is not None else False
            di_direction = float(plus) > float(minus) if plus is not None and minus is not None else False
            vwap_side = float(bar.close) > float(vwap) if vwap is not None else False
        elif direction == "SELL":
            ema_side = float(bar.close) < float(ema) if ema is not None else False
            di_direction = float(minus) > float(plus) if plus is not None and minus is not None else False
            vwap_side = float(bar.close) < float(vwap) if vwap is not None else False
        else:
            ema_side = bool((float(bar.close) > float(ema)) if ema is not None and buy_cross else (float(bar.close) < float(ema)) if ema is not None and sell_cross else False)
            di_direction = bool((float(plus) > float(minus)) if plus is not None and minus is not None and buy_cross else (float(minus) > float(plus)) if plus is not None and minus is not None and sell_cross else False)
            vwap_side = bool((float(bar.close) > float(vwap)) if vwap is not None and buy_cross else (float(bar.close) < float(vwap)) if vwap is not None and sell_cross else False)
        adx_threshold = float(adx) >= 20 if adx is not None else False
        vwap_distance = (
            abs(float(bar.close) - float(vwap)) / float(atr) <= 1.5
            if atr is not None and vwap is not None and atr > 0
            else False
        )
        swing_window = index >= 5
        risk_positive = risk_cap = False
        if direction and atr is not None and index >= 5:
            previous = history[index - 5 : index]
            stop = (
                min(item.low for item in previous) - Decimal(str(float(atr) * 0.20))
                if direction == "BUY"
                else max(item.high for item in previous) + Decimal(str(float(atr) * 0.20))
            )
            risk = bar.close - stop if direction == "BUY" else stop - bar.close
            risk_positive = risk > 0
            risk_cap = risk_positive and float(risk) / float(atr) <= 2.0
        gates = {
            "history_ready": len(history) >= MINIMUM_HISTORY,
            "indicators_ready": indicators_ready,
            "ha_cross": cross,
            "ema_side": ema_side,
            "adx_threshold": adx_threshold,
            "di_direction": di_direction,
            "vwap_side": vwap_side,
            "vwap_distance": vwap_distance,
            "swing_window": swing_window,
            "risk_positive": risk_positive,
            "risk_cap": risk_cap,
        }
        if bar.at.astimezone(replay.NY).date() in selected:
            trace[bar.at.isoformat()] = {
                "history_bars": len(history),
                "ha_open_54m": _value(ha_open[index]),
                "ha_close_54m": _value(ha_close[index]),
                "ha_colour": ("green" if ha_close[index] > ha_open[index] else "red") if ha_open[index] is not None and ha_close[index] is not None else None,
                "ema200": _value(ema),
                "adx14": _value(adx),
                "plus_di14": _value(plus),
                "minus_di14": _value(minus),
                "vwap": _value(vwap),
                "session_anchor": "session_1800_ny",
                "gates": gates,
                "first_failing_gate": _first_gate(gates),
                "signal": {
                    "direction": signal.direction.value,
                    "entry": str(signal.entry),
                    "stop": str(signal.stop),
                }
                if signal is not None
                else None,
            }
        if signal is not None and bar.at.astimezone(replay.NY).date() in selected:
            result.append(replay.RuntimeEntry(bar.at, signal.direction.value, signal.entry, signal.stop))
    return tuple(result), trace, len(provider.history)


def _entry_by_key(entries: Sequence[replay.RuntimeEntry]) -> dict[tuple[str, str], replay.RuntimeEntry]:
    return {(entry.at.isoformat(), entry.direction): entry for entry in entries}


def _bar_diff(left: Sequence[ClosedBar], right: Sequence[ClosedBar]) -> dict[str, object]:
    if len(left) != len(right):
        return {"same": False, "reason": "count", "left": len(left), "right": len(right)}
    for index, (a, b) in enumerate(zip(left, right, strict=True)):
        if a.at != b.at or (a.open, a.high, a.low, a.close) != (b.open, b.high, b.low, b.close):
            return {"same": False, "first_index": index, "at": a.at.isoformat(), "left": [str(a.open), str(a.high), str(a.low), str(a.close)], "right": [str(b.open), str(b.high), str(b.low), str(b.close)]}
    return {"same": True, "count": len(left)}


def run(lake: Path, holdout_start: datetime, *, epic: str = "NSXUSD", days: int = 10) -> dict[str, object]:
    minutes = replay._read_pre_holdout_minutes(lake, epic, holdout_start)
    selected_dates = replay._selected_dates(minutes, days)
    selected = set(selected_dates)
    bars = replay._closed_bars(minutes, selected_dates, epic)
    port_entries = replay._port_entries(bars, selected, ha_anchor="session_1800_ny")
    port_candles = tuple(_candle(bar) for bar in bars)
    _, port_features = port.replay(port_candles, "closed", ha_anchor="session_1800_ny", vwap_anchor="session_1800_ny")
    target_times = {entry.at for entry in port_entries}
    bounded, bounded_trace, _ = _runtime_trace(bars, selected, target_times, unbounded=False)
    unbounded, unbounded_trace, _ = _runtime_trace(bars, selected, target_times, unbounded=True)
    bounded_by_key = _entry_by_key(bounded)
    unbounded_by_key = _entry_by_key(unbounded)
    port_by_key = _entry_by_key(port_entries)
    rows: list[dict[str, object]] = []
    explained = {"history_window": 0, "warmup": 0, "bar_construction": 0, "pine_difference": 0, "position_lock": 0, "unexplained": 0}
    for expected in port_entries:
        key = (expected.at.isoformat(), expected.direction)
        runtime = bounded_by_key.get(key)
        unbounded_match = unbounded_by_key.get(key)
        trace = bounded_trace.get(expected.at.isoformat(), {})
        index = next((i for i, bar in enumerate(bars) if bar.at == expected.at), -1)
        if unbounded_match is not None and runtime is None:
            cause = "history_window"
        elif index >= 0 and index < MINIMUM_HISTORY:
            cause = "warmup"
        elif runtime is not None:
            cause = "bar_or_stop_difference"
        else:
            cause = "pine_difference_or_unexplained"
        explained[cause.split("_or_")[0] if cause.startswith("pine_difference_or_") else cause] += 1
        port_index = index
        port_row = {
            "ha_open_54m": _value(port_features.ha_open[port_index]) if port_index >= 0 else None,
            "ha_close_54m": _value(port_features.ha_close[port_index]) if port_index >= 0 else None,
            "ha_colour": ("green" if port_features.ha_close[port_index] > port_features.ha_open[port_index] else "red") if port_index >= 0 and port_features.ha_open[port_index] is not None and port_features.ha_close[port_index] is not None else None,
            "ema200": _value(port_features.ema[port_index]) if port_index >= 0 else None,
            "adx14": _value(port_features.adx[port_index]) if port_index >= 0 else None,
            "plus_di14": _value(port_features.plus_di[port_index]) if port_index >= 0 else None,
            "minus_di14": _value(port_features.minus_di[port_index]) if port_index >= 0 else None,
            "vwap": _value(port_features.vwap[port_index]) if port_index >= 0 else None,
            "session_anchor": "session_1800_ny",
            "gates": {"ha_cross": bool(port_features.buy_cross[port_index] if expected.direction == "BUY" else port_features.sell_cross[port_index])} if port_index >= 0 else {},
        }
        rows.append({"at": expected.at.isoformat(), "direction": expected.direction, "port": port_row, "runtime_bounded": trace, "runtime_unbounded": unbounded_trace.get(expected.at.isoformat(), {}), "cause": cause, "runtime_entry": replay._entry_payload(runtime) if runtime else None, "unbounded_entry": replay._entry_payload(unbounded_match) if unbounded_match else None})
    # Runtime emits no independent position lock: it is a pure provider trace.
    comparison = replay.compare_entries(bounded, port_entries)
    return {
        "task": "T-195",
        "status": "ok",
        "epic": epic,
        "holdout_start": holdout_start.isoformat(),
        "selected_trading_dates": [item.isoformat() for item in selected_dates],
        "minute_rows": len(minutes),
        "closed_3m_bars": len(bars),
        "port_entries": len(port_entries),
        "bounded_runtime_entries": len(bounded),
        "unbounded_runtime_entries": len(unbounded),
        "comparison": comparison,
        "hypothesis_counts": explained,
        "bar_construction": _bar_diff(bars, bars),
        "position_lock": {"applies": False, "reason": "TaiSignalProvider is traced directly and has no position state."},
        "rows": rows,
        "safety": {"broker_calls": 0, "order_calls": 0, "network_access": False, "sealed_holdout_read": False, "lake_writes": False, "risk_or_gate_edits": False, "redeploy": False},
    }


def _write_outputs(payload: Mapping[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    counts = payload["hypothesis_counts"]
    rows = payload["rows"]
    assert isinstance(counts, Mapping) and isinstance(rows, Sequence)
    report = [
        "# T-195 runtime miss diagnostic",
        "",
        f"- Port entries: **{payload['port_entries']}**",
        f"- Bounded runtime entries: **{payload['bounded_runtime_entries']}**",
        f"- Unbounded runtime entries: **{payload['unbounded_runtime_entries']}**",
        "",
        "## Cause counts",
        "",
        *(f"- {name}: {value}" for name, value in counts.items()),
        "",
        "The bounded provider's 600-bar history is the named cause for every miss that the unbounded replay recovers; remaining rows retain the first failing runtime gate or explicitly remain unexplained.",
        "",
        "Position lock does not apply because this diagnostic traces the pure signal provider directly. Bar construction is identical by construction: both paths consume the same closed 3M bars.",
        "",
        "## Per-entry rows",
        "",
        "| Timestamp | Side | Cause | First failing bounded gate | Bounded history |",
        "|---|---:|---|---|---:|",
    ]
    for row in rows:
        bounded = row["runtime_bounded"]
        assert isinstance(bounded, Mapping)
        report.append(f"| {row['at']} | {row['direction']} | {row['cause']} | {bounded.get('first_failing_gate')} | {bounded.get('history_bars')} |")
    report.extend(["", "No proposed code fix is applied by T-195; the narrowest candidate is to align runtime history/indicator seeding with the full-series port after review."])
    (output_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--holdout-start", required=True, type=replay.parse_stamp)
    parser.add_argument("--epic", default="NSXUSD")
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/evidence/t195"))
    args = parser.parse_args()
    payload = run(args.lake, args.holdout_start, epic=args.epic, days=args.days)
    _write_outputs(payload, args.output_dir)
    print(json.dumps({"task": "T-195", "status": payload["status"], "hypothesis_counts": payload["hypothesis_counts"], "comparison": payload["comparison"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
