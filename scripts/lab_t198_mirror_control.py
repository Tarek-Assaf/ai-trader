#!/usr/bin/env python3
"""Run the read-only T-198 opposite-direction control for the Tai port."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trader.lab.backtest import CostModel
from trader.lab.runner import pessimistic_cost_model

try:
    from scripts import lab_t180_hist_tai as harness
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import lab_t180_hist_tai as harness


INSTRUMENTS = ("NSXUSD", "XAUUSD")
TIMEFRAMES = (1, 3, 5)
LABEL = "mirror control of the port, diagnostic only"


def _gross(reason: str | None, highest: int) -> float | None:
    if reason is None:
        return None
    if reason == "tp3":
        return 3.0
    if reason == "be":
        return 0.0
    return -1.0


def _mirror_one(bars: tuple[Any, ...], original: Any) -> dict[str, Any]:
    """Simulate one inverted trade from the original entry bar through the bars."""
    index = original.entry_index
    entry_bar = bars[index]
    direction = "short" if original.direction == "long" else "long"
    entry = float(entry_bar.open)
    risk = abs(float(original.entry_price) - float(original.stop_price))
    stop = entry + risk if direction == "short" else entry - risk
    highest = 0
    exit_index: int | None = None
    reason: str | None = None
    active_stop = stop
    # Keep each control on the same entry-to-exit opportunity horizon as its
    # source trade.  This makes the paired replay linear in the bar series and
    # prevents an open mirror from consuming the remainder of full history.
    limit = original.exit_index + 1 if original.exit_index is not None else len(bars)
    for i in range(index, limit):
        bar = bars[i]
        long = direction == "long"
        hit_stop = bar.low <= active_stop if long else bar.high >= active_stop
        hit1 = bar.high >= entry + risk if long else bar.low <= entry - risk
        hit2 = bar.high >= entry + risk * 2 if long else bar.low <= entry - risk * 2
        hit3 = bar.high >= entry + risk * 3 if long else bar.low <= entry - risk * 3
        if highest == 0:
            if hit3:
                highest, reason = 3, "tp3"
            elif hit2:
                highest, active_stop = 2, entry
            elif hit1:
                highest, active_stop = 1, entry
            elif hit_stop:
                reason = "stop"
        elif hit3:
            highest, reason = 3, "tp3"
        elif hit2:
            highest, active_stop = 2, entry
        elif hit_stop:
            reason = "be"
        if reason is not None:
            exit_index = i
            break
    exit_time = bars[exit_index].ts_utc.isoformat() if exit_index is not None else None
    return {
        "entry": entry_bar.ts_utc.isoformat(),
        "exit": exit_time,
        "side": direction,
        "entry_price": entry,
        "stop_price": stop,
        "risk_points": risk,
        "exit_reason": reason,
        "highest_target": highest,
        "gross_r": _gross(reason, highest),
    }


def _with_cost(trade: dict[str, Any], instrument: str, costs: Any) -> dict[str, Any]:
    spread, slippage = costs.for_bar(instrument, datetime.fromisoformat(trade["entry"]))
    cost_points = spread + 2.0 * slippage
    risk = float(trade["risk_points"])
    trade = dict(trade)
    trade["spread_points"] = spread
    trade["slippage_points"] = slippage
    trade["cost_points"] = cost_points
    trade["r"] = (
        None
        if trade["gross_r"] is None or risk <= 0
        else float(trade["gross_r"]) - cost_points / risk
    )
    return trade


def _metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(item["r"]) for item in trades if item.get("r") is not None]
    gross = [float(item["gross_r"]) for item in trades if item.get("gross_r") is not None]
    positive = sum(value for value in values if value > 0)
    negative = sum(value for value in values if value < 0)
    running = peak = drawdown = 0.0
    for value in values:
        running += value
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    return {
        "trades": len(trades),
        "completed": len(gross),
        "win_rate": sum(value > 0 for value in gross) / len(gross) if gross else None,
        "net_r": sum(values),
        "profit_factor": positive / abs(negative) if negative else None,
        "max_drawdown_r": drawdown,
    }


def _per_year(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[trade["entry"][:4]].append(trade)
    return {year: _metrics(items) for year, items in sorted(groups.items())}


def _cell(
    lake: Path,
    instrument: str,
    timeframe: int,
    start: datetime,
    end: datetime,
    configured: Any,
) -> dict[str, Any]:
    bars = harness._bars_for_window(lake, instrument, timeframe, start, end)
    raw_trades, _ = harness._replay(bars, timeframe)
    originals = [item for item in raw_trades if harness._in_window(item.entry_time, start, end)]
    mirrors = [_mirror_one(bars, item) for item in originals]
    original_configured = [
        harness._trade_payload(item, instrument, configured) for item in originals
    ]
    zero = CostModel(spread_points={}, slippage_points={}, provisional=True)
    original_zero = [harness._trade_payload(item, instrument, zero) for item in originals]
    mirror_configured = [_with_cost(item, instrument, configured) for item in mirrors]
    mirror_zero = [_with_cost(item, instrument, zero) for item in mirrors]
    return {
        "instrument": instrument,
        "timeframe_minutes": timeframe,
        "source_rows": len(bars),
        "trade_count": len(originals),
        "mirror_trade_count": len(mirrors),
        "signal_count": None,
        "original": {
            "configured": _metrics(original_configured),
            "zero_cost": _metrics(original_zero),
            "per_year": _per_year(original_configured),
        },
        "mirror": {
            "configured": _metrics(mirror_configured),
            "zero_cost": _metrics(mirror_zero),
            "per_year": _per_year(mirror_configured),
        },
        "count_match": len(originals) == len(mirrors),
        "status": "ok",
    }


def run(
    lake: Path,
    start: datetime,
    end: datetime,
    holdout_start: datetime,
    config: Path,
) -> dict[str, Any]:
    harness.guard_window(start, end, holdout_start)
    configured = pessimistic_cost_model(config)
    harness._SOURCE_CACHE.clear()
    cells = [
        _cell(lake, instrument, timeframe, start, end, configured)
        for instrument in INSTRUMENTS
        for timeframe in TIMEFRAMES
    ]
    return {
        "task": "T-198",
        "generated_at": datetime.now(UTC).isoformat(),
        "label": LABEL,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "holdout_guard": {
            "tested": True,
            "holdout_start": holdout_start.isoformat(),
        },
        "cells": cells,
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "sealed_holdout_read": False,
            "lake_writes": 0,
            "production_state_writes": 0,
            "tuning": False,
        },
    }


def _fmt(value: Any) -> str:
    return "—" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


def report(payload: dict[str, Any]) -> str:
    cells = payload["cells"]
    edge = all(
        cell["original"]["configured"]["net_r"] > cell["mirror"]["configured"]["net_r"]
        for cell in cells
        if cell["trade_count"]
    )
    lines = [
        "# T-198 Tai mirror control",
        "",
        "**Verdict:** "
        + (
            "The original beats the mirror in every non-empty cell; this is a directional "
            "edge diagnostic."
            if edge
            else "The original does not beat the mirror in every non-empty cell; no "
            "directional edge is claimed."
        ),
        "This is a mirror control of the port, diagnostic only. It reuses every original entry "
        "bar and inverts direction, stop and targets.",
        "",
        f"Window: `{payload['window']['start']}` through `{payload['window']['end']}` "
        "(end exclusive).",
        "",
        "## Overall configured-cost and zero-cost results",
        "",
        "| Instrument | TF | Original trades | Mirror trades | Original net R | Mirror net R | "
        "Original PF | Mirror PF | Count match |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for cell in cells:
        original = cell["original"]
        mirror = cell["mirror"]
        lines.append(
            f"| {cell['instrument']} | {cell['timeframe_minutes']}m | {cell['trade_count']} | "
            f"{cell['mirror_trade_count']} | {_fmt(original['configured']['net_r'])} | "
            f"{_fmt(mirror['configured']['net_r'])} | "
            f"{_fmt(original['configured']['profit_factor'])} | "
            f"{_fmt(mirror['configured']['profit_factor'])} | {cell['count_match']} |"
        )
    lines += [
        "",
        "Zero-cost counterfactuals are included in summary.json. Mirror counts must equal "
        "original counts; a false value is a failed run.",
        "",
        "## Per-year configured net R (original / mirror; trades in parentheses)",
        "",
    ]
    for cell in cells:
        lines += [
            f"### {cell['instrument']} {cell['timeframe_minutes']}m",
            "| Year | Original | Mirror |",
            "| ---: | ---: | ---: |",
        ]
        years = sorted(set(cell["original"]["per_year"]) | set(cell["mirror"]["per_year"]))
        for year in years:
            original = cell["original"]["per_year"].get(year, {})
            mirror = cell["mirror"]["per_year"].get(year, {})
            lines.append(
                f"| {year} | {_fmt(original.get('net_r'))} ({original.get('trades', 0)}) | "
                f"{_fmt(mirror.get('net_r'))} ({mirror.get('trades', 0)}) |"
            )
        lines.append("")
    lines += ["## Safety", "", json.dumps(payload["safety"], sort_keys=True), ""]
    lines.append(
        "The holdout guard was exercised before reading the lake. No broker, network, order, "
        "tuning, risk, gate, production-state or lake write path is present."
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--start", default="2020-10-17")
    parser.add_argument("--end", default="2026-10-17")
    parser.add_argument("--holdout-start", default="2026-10-17T04:00:00+00:00")
    parser.add_argument("--config", type=Path, default=Path("config/strategy_lab.yaml"))
    args = parser.parse_args()
    payload = run(
        args.lake,
        harness.parse_date(args.start),
        harness.parse_date(args.end),
        harness.parse_stamp(args.holdout_start),
        args.config,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report(payload), encoding="utf-8")
    print(
        json.dumps({"task": "T-198", "cells": len(payload["cells"]), "safety": payload["safety"]})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
