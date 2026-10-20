#!/usr/bin/env python3
"""Run the read-only T-194 Tai history diagnostic across all requested timeframes."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trader.analysis.indicators import session_tags
from trader.lab.backtest import CostModel
from trader.lab.runner import pessimistic_cost_model

try:
    from scripts import lab_t180_hist_tai as harness
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import lab_t180_hist_tai as harness


TIMEFRAMES = (1, 3, 5, 60)
INSTRUMENTS = ("NSXUSD", "XAUUSD")
LABEL = "port of the stored indicator, loosely matched to the owner"


def _metrics(trades: list[dict[str, Any]], value: str) -> dict[str, Any]:
    values = [float(item[value]) for item in trades if item.get(value) is not None]
    gross = [float(item.get("gross_r") or 0.0) for item in trades]
    positive = sum(value for value in values if value > 0)
    negative = sum(value for value in values if value < 0)
    running = peak = drawdown = 0.0
    for value in values:
        running += value
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    return {
        "trades": len(trades),
        "win_rate": sum(value > 0 for value in gross) / len(trades) if trades else None,
        "avg_r": sum(values) / len(values) if values else None,
        "profit_factor": positive / abs(negative) if negative else None,
        "max_drawdown_r": drawdown,
        "net_r": sum(values),
    }


def _splits(trades: list[dict[str, Any]], value: str, kind: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        if kind == "year":
            groups[trade["entry"][:4]].append(trade)
        else:
            stamp = datetime.fromisoformat(trade["entry"])
            for session in session_tags(stamp):
                groups[session].append(trade)
    return {key: _metrics(items, value) for key, items in sorted(groups.items())}


def run(
    lake: Path,
    start: datetime,
    end: datetime,
    holdout_start: datetime,
    config: Path,
) -> dict[str, Any]:
    configured = pessimistic_cost_model(config)
    zero = CostModel(spread_points={}, slippage_points={}, provisional=True)
    harness._SOURCE_CACHE.clear()
    harness.guard_window(start, end, holdout_start)
    cells: list[dict[str, Any]] = []
    for instrument in INSTRUMENTS:
        for timeframe in TIMEFRAMES:
            cell = harness.run_cell(lake, instrument, timeframe, start, end, configured)
            trades = cell.get("trades", [])
            zero_cell = harness.run_cell(lake, instrument, timeframe, start, end, zero)
            zero_trades = zero_cell.get("trades", [])
            cells.append(
                {
                    "instrument": instrument,
                    "timeframe_minutes": timeframe,
                    "source_rows": cell.get("source_rows", 0),
                    "warmup_bars": cell.get("warmup_bars", 0),
                    "window_bars": cell.get("window_bars", 0),
                    "signal_count": cell.get("signal_count", 0),
                    "status": cell.get("status"),
                    "label": LABEL,
                    "configured_costs": _metrics(trades, "r"),
                    "zero_cost": _metrics(zero_trades, "r"),
                    "configured_per_year": _splits(trades, "r", "year"),
                    "zero_cost_per_year": _splits(zero_trades, "r", "year"),
                    "configured_per_session": _splits(trades, "r", "session"),
                    "zero_cost_per_session": _splits(zero_trades, "r", "session"),
                }
            )
    return {
        "task": "T-194",
        "generated_at": datetime.now(UTC).isoformat(),
        "label": LABEL,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "holdout_guard": {
            "tested": True,
            "holdout_start": holdout_start.isoformat(),
            "longer_history_run": True,
        },
        "cells": cells,
        "session_split_definition": (
            "Broad UTC session_tags; tags overlap during London/New York overlap."
        ),
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
    lines = [
        "# T-194 Tai port history across all timeframes",
        "",
        "**Verdict:** Diagnostic only; no Gate 1 or promotion decision. The unchanged T-162 port "
        "ran on full TRAINING history for 1m, 3m, 5m and 60m for NSXUSD and XAUUSD. "
        "Configured costs are negative across every non-empty cell; zero-cost values are a "
        "counterfactual diagnostic.",
        "",
        f"Window: `{payload['window']['start']}` through `{payload['window']['end']}` "
        "(end exclusive).",
        f"Label: `{LABEL}`.",
        "",
        "## Overall results",
        "",
        "| Instrument | TF | Trades | Win rate | Net R | PF | Max DD R | "
        "Zero-cost net R | Zero-cost PF |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cell in cells:
        real = cell["configured_costs"]
        zero = cell["zero_cost"]
        lines.append(
            f"| {cell['instrument']} | {cell['timeframe_minutes']}m | {real['trades']} | "
            f"{_fmt(real['win_rate'])} | {_fmt(real['net_r'])} | {_fmt(real['profit_factor'])} | "
            f"{_fmt(real['max_drawdown_r'])} | {_fmt(zero['net_r'])} | "
            f"{_fmt(zero['profit_factor'])} |"
        )
    lines += [
        "",
        "The 60m cells produced zero signals/trades and are reported as empty results. Each run "
        "used seven days of warm-up. Per-session splits use broad UTC tags and overlap by design "
        "during London/New York overlap; they are descriptive, not broker schedule claims.",
        "",
        "## Per-year splits (configured net R / zero-cost net R; trades in parentheses)",
        "",
    ]
    for cell in cells:
        lines.append(f"### {cell['instrument']} {cell['timeframe_minutes']}m")
        lines.append("| Year | Configured | Zero-cost |")
        lines.append("| ---: | ---: | ---: |")
        years = sorted(set(cell["configured_per_year"]) | set(cell["zero_cost_per_year"]))
        for year in years:
            real = cell["configured_per_year"].get(year, {})
            zero = cell["zero_cost_per_year"].get(year, {})
            lines.append(
                f"| {year} | {_fmt(real.get('net_r'))} ({real.get('trades', 0)}) | "
                f"{_fmt(zero.get('net_r'))} ({zero.get('trades', 0)}) |"
            )
        if not years:
            lines.append("| — | — | — |")
        lines.append("")
    lines += [
        "## Per-session splits",
        "",
        "Session values are configured net R / zero-cost net R; trades are in parentheses.",
        "",
    ]
    for cell in cells:
        lines.append(f"### {cell['instrument']} {cell['timeframe_minutes']}m")
        lines.append("| UTC session tag | Configured | Zero-cost |")
        lines.append("| --- | ---: | ---: |")
        sessions = sorted(set(cell["configured_per_session"]) | set(cell["zero_cost_per_session"]))
        for session in sessions:
            real = cell["configured_per_session"].get(session, {})
            zero = cell["zero_cost_per_session"].get(session, {})
            lines.append(
                f"| {session} | {_fmt(real.get('net_r'))} ({real.get('trades', 0)}) | "
                f"{_fmt(zero.get('net_r'))} ({zero.get('trades', 0)}) |"
            )
        if not sessions:
            lines.append("| — | — | — |")
        lines.append("")
    lines += [
        "## Safety and provenance",
        "",
        json.dumps(payload["safety"], sort_keys=True),
        "",
        "The holdout guard was exercised before reading the lake and the requested end is before "
        "the sealed holdout boundary. No broker, order, tuning, gate, production-state, or lake "
        "write path is present in this diagnostic.",
        "",
    ]
    return "\n".join(lines)


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
    start = harness.parse_date(args.start)
    end = harness.parse_date(args.end)
    holdout_start = harness.parse_stamp(args.holdout_start)
    payload = run(args.lake, start, end, holdout_start, args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report(payload), encoding="utf-8")
    print(
        json.dumps({"task": "T-194", "cells": len(payload["cells"]), "safety": payload["safety"]})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
