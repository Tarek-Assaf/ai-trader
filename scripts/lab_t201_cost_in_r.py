#!/usr/bin/env python3
"""Measure configured cost in R against Tai stop distances (T-201).

The command is deliberately read-only.  It reuses the historical Tai harness from
T-180 and emits percentile tables plus the three requested net-R populations.  The
broker's tick and minimum-stop values are intentionally optional: this repository
does not contain owner-verified values, so the report says when that filter cannot
be evaluated instead of inventing a value.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trader.lab.runner import pessimistic_cost_model

try:
    from scripts import lab_t180_hist_tai as harness
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import lab_t180_hist_tai as harness


INSTRUMENTS = ("NSXUSD", "XAUUSD")
TIMEFRAMES = (1, 3, 5)
OWNER_POINT_VALUE = 20.0
DEFAULT_START = datetime(2020, 10, 17, 4, tzinfo=UTC)
DEFAULT_END = datetime(2026, 10, 17, 4, tzinfo=UTC)
DEFAULT_HOLDOUT_START = DEFAULT_END


def _percentile(values: Iterable[float], probability: float) -> float | None:
    """Return an inclusive linear percentile, or ``None`` for no observations."""
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in trades if item.get("gross_r") is not None]
    gross = [float(item["gross_r"]) for item in completed]
    configured = [float(item["configured_r"]) for item in completed]
    zero = [float(item["gross_r"]) for item in completed]
    return {
        "trades": len(trades),
        "completed": len(completed),
        "gross_net_r": sum(gross),
        "zero_cost_net_r": sum(zero),
        "configured_net_r": sum(configured),
        "zero_cost_r_per_trade": sum(zero) / len(zero) if zero else None,
        "configured_r_per_trade": sum(configured) / len(configured) if configured else None,
    }


def _per_year(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade["entry"][:4])].append(trade)
    return {year: _metrics(items) for year, items in sorted(groups.items())}


def _distribution(trades: list[dict[str, Any]], *, cost_key: str = "cost_points") -> dict[str, Any]:
    valid = [
        item
        for item in trades
        if float(item.get("risk_points", 0.0)) > 0 and item.get(cost_key) is not None
    ]
    risks = [float(item["risk_points"]) for item in valid]
    ratios = [float(item[cost_key]) / float(item["risk_points"]) for item in valid]
    return {
        "trades": len(valid),
        "risk_points": {
            "p01": _percentile(risks, 0.01),
            "p05": _percentile(risks, 0.05),
            "p25": _percentile(risks, 0.25),
            "median": _percentile(risks, 0.50),
            "p75": _percentile(risks, 0.75),
            "p95": _percentile(risks, 0.95),
        },
        "cost_over_r": {
            "p01": _percentile(ratios, 0.01),
            "p05": _percentile(ratios, 0.05),
            "p25": _percentile(ratios, 0.25),
            "median": _percentile(ratios, 0.50),
            "p75": _percentile(ratios, 0.75),
            "p95": _percentile(ratios, 0.95),
        },
        "share_cost_over_0_25_r": sum(value > 0.25 for value in ratios) / len(ratios)
        if ratios
        else None,
        "share_cost_over_1_r": sum(value > 1.0 for value in ratios) / len(ratios)
        if ratios
        else None,
    }


def _with_cost(trade: dict[str, Any], instrument: str, costs: Any) -> dict[str, Any]:
    spread, slippage = costs.for_bar(instrument, datetime.fromisoformat(trade["entry"]))
    cost_points = float(spread) + 2.0 * float(slippage)
    risk = float(trade["risk_points"])
    result = dict(trade)
    result["spread_points"] = float(spread)
    result["slippage_points"] = float(slippage)
    result["cost_points"] = cost_points
    result["configured_r"] = (
        None
        if result.get("gross_r") is None or risk <= 0
        else float(result["gross_r"]) - cost_points / risk
    )
    return result


def _t200_passes(trade: dict[str, Any], tick_size: float | None) -> bool | None:
    """Apply the T-200 spread ceiling when the broker tick is known."""
    if tick_size is None:
        return None
    risk = float(trade["risk_points"])
    spread = float(trade["spread_points"])
    return spread <= max(0.25 * risk, 2.0 * tick_size)


def _owner_trades(path: Path, *, cost_points: float = 4.0) -> list[dict[str, Any]]:
    """Pair owner entry/exit rows and measure the stop leg in points."""
    rows: dict[str, dict[str, str]] = defaultdict(dict)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            number = row.get("Trade number", "").strip()
            kind = row.get("Type", "")
            if number and row.get("Price USD"):
                rows[number]["entry" if kind.startswith("Entry") else "exit"] = row["Price USD"]
                rows[number]["pnl"] = row.get("Net PnL USD", "0")
                rows[number]["date"] = row.get("Date and time", "")
    result: list[dict[str, Any]] = []
    for number, row in rows.items():
        if "entry" not in row or "exit" not in row:
            continue
        risk = abs(float(row["exit"]) - float(row["entry"]))
        gross_r = float(row.get("pnl", "0") or 0.0) / (risk * OWNER_POINT_VALUE) if risk else None
        result.append(
            {
                "trade_number": number,
                "entry": row.get("date", ""),
                "risk_points": risk,
                "cost_points": cost_points,
                "gross_r": gross_r,
                "configured_r": gross_r - cost_points / risk
                if gross_r is not None and risk
                else None,
            }
        )
    return result


def _cell(
    lake: Path,
    instrument: str,
    timeframe: int,
    start: datetime,
    end: datetime,
    configured: Any,
    tick_size: float | None,
    minimum_stop: float | None,
) -> dict[str, Any]:
    bars = harness._bars_for_window(lake, instrument, timeframe, start, end)
    raw_trades, _ = harness._replay(bars, timeframe)
    trades = [
        _with_cost(harness._trade_payload(item, instrument, configured), instrument, configured)
        for item in raw_trades
        if harness._in_window(item.entry_time, start, end)
    ]
    guard = [item for item in trades if _t200_passes(item, tick_size) is True]
    minimum = (
        [item for item in trades if float(item["risk_points"]) >= minimum_stop]
        if minimum_stop is not None
        else []
    )
    return {
        "instrument": instrument,
        "timeframe_minutes": timeframe,
        "source_rows": len(bars),
        "trade_count": len(trades),
        "distribution": _distribution(trades),
        "zero_cost_vs_configured": _metrics(trades),
        "t200_guard": {
            "tick_size": tick_size,
            "available": tick_size is not None,
            "trade_count": len(guard) if tick_size is not None else None,
            "metrics": _metrics(guard) if tick_size is not None else None,
        },
        "minimum_stop": {
            "value": minimum_stop,
            "available": minimum_stop is not None,
            "trade_count": len(minimum) if minimum_stop is not None else None,
            "metrics": _metrics(minimum) if minimum_stop is not None else None,
        },
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _pine_filters(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    keywords = ("stop", "atr", "range")
    lines = [
        {"line": index, "text": line.strip()}
        for index, line in enumerate(text.splitlines(), 1)
        if any(keyword in line.lower() for keyword in keywords)
    ]
    minimum_stop = [
        line for line in lines if "min" in line["text"].lower() and "stop" in line["text"].lower()
    ]
    atr_floor = [
        line
        for line in lines
        if "atr" in line["text"].lower()
        and ("min" in line["text"].lower() or "floor" in line["text"].lower())
    ]
    range_filter = [
        line
        for line in lines
        if "range" in line["text"].lower() and any(op in line["text"] for op in (">", "<"))
    ]
    return {
        "minimum_stop_lines": minimum_stop,
        "atr_floor_lines": atr_floor,
        "bar_range_filter_lines": range_filter,
        "conclusion": "filters found"
        if minimum_stop or atr_floor or range_filter
        else "none found",
    }


def run(
    lake: Path,
    owner_trades_path: Path,
    pine_path: Path,
    config: Path,
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
    holdout_start: datetime = DEFAULT_HOLDOUT_START,
    tick_sizes: dict[str, float | None] | None = None,
    minimum_stops: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    harness.guard_window(start, end, holdout_start)
    configured = pessimistic_cost_model(config)
    harness._SOURCE_CACHE.clear()
    tick_sizes = tick_sizes or {instrument: None for instrument in INSTRUMENTS}
    minimum_stops = minimum_stops or {instrument: None for instrument in INSTRUMENTS}
    cells = [
        _cell(
            lake,
            instrument,
            timeframe,
            start,
            end,
            configured,
            tick_sizes.get(instrument),
            minimum_stops.get(instrument),
        )
        for instrument in INSTRUMENTS
        for timeframe in TIMEFRAMES
    ]
    owners = _owner_trades(owner_trades_path)
    return {
        "task": "T-201",
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "cells": cells,
        "owner_trades": {
            "path": str(owner_trades_path),
            "trade_count": len(owners),
            "distribution": _distribution(owners),
            "metrics": _metrics(owners),
        },
        "pine_filters": _pine_filters(pine_path),
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "sealed_holdout_read": False,
            "lake_writes": 0,
            "production_state_writes": 0,
            "config_edits": 0,
            "tuning": False,
        },
    }


def report(payload: dict[str, Any]) -> str:
    lines = [
        "# T-201 cost per trade in R by stop size",
        "",
        "This is a read-only diagnostic. Configured cost is spread + 2 × slippage;",
        "zero-cost R is gross R.",
        (
            f"Training window: `{payload['window']['start']}` through "
            f"`{payload['window']['end']}` (end exclusive)."
        ),
        "",
        "## Risk and cost distributions",
        "",
        "| Instrument | TF | Trades | Risk p1 | p5 | p25 | Median | p75 | p95 | "
        "Cost/R median | >0.25R | >1R |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cell in payload["cells"]:
        risk = cell["distribution"]["risk_points"]
        ratio = cell["distribution"]["cost_over_r"]
        lines.append(
            f"| {cell['instrument']} | {cell['timeframe_minutes']}m | "
            f"{cell['distribution']['trades']} | {_fmt(risk['p01'])} | {_fmt(risk['p05'])} | "
            f"{_fmt(risk['p25'])} | {_fmt(risk['median'])} | {_fmt(risk['p75'])} | "
            f"{_fmt(risk['p95'])} | {_fmt(ratio['median'])} | "
            f"{_fmt(cell['distribution']['share_cost_over_0_25_r'])} | "
            f"{_fmt(cell['distribution']['share_cost_over_1_r'])} |"
        )
    owner = payload["owner_trades"]["distribution"]
    lines += [
        "",
        "## Owner 31-trade comparison",
        "",
        (
            f"Owner rows paired into `{payload['owner_trades']['trade_count']}` trades; "
            "risk is the absolute entry-to-stop-leg price move."
        ),
        "",
        "| Population | Trades | Risk p1 | p5 | p25 | Median | p75 | p95 | "
        "Cost/R median | >0.25R | >1R |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| Owner replay | {owner['trades']} | {_fmt(owner['risk_points']['p01'])} | "
        f"{_fmt(owner['risk_points']['p05'])} | {_fmt(owner['risk_points']['p25'])} | "
        f"{_fmt(owner['risk_points']['median'])} | {_fmt(owner['risk_points']['p75'])} | "
        f"{_fmt(owner['risk_points']['p95'])} | {_fmt(owner['cost_over_r']['median'])} | "
        f"{_fmt(owner['share_cost_over_0_25_r'])} | {_fmt(owner['share_cost_over_1_r'])} |",
        "",
        "## Net-R populations",
        "",
        "The cell JSON contains zero-cost/configured totals for all trades, the T-200 guard "
        "subset when tick size is known, and the minimum-stop subset when the broker value "
        "is known.",
        "",
    ]
    for cell in payload["cells"]:
        base = cell["zero_cost_vs_configured"]
        guard = cell["t200_guard"]
        minimum = cell["minimum_stop"]
        lines.append(f"### {cell['instrument']} {cell['timeframe_minutes']}m")
        lines.append(
            "| Population | Kept | Zero-cost net R | Configured net R | Configured R/trade |"
        )
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        lines.append(
            f"| All | {base['trades']} | {_fmt(base['zero_cost_net_r'])} | "
            f"{_fmt(base['configured_net_r'])} | {_fmt(base['configured_r_per_trade'])} |"
        )
        for label, item in (("T-200 spread guard", guard), ("Minimum stop", minimum)):
            metrics = item["metrics"]
            if metrics is None:
                lines.append(f"| {label} | unavailable | — | — | — |")
            else:
                lines.append(
                    f"| {label} | {metrics['trades']} | {_fmt(metrics['zero_cost_net_r'])} | "
                    f"{_fmt(metrics['configured_net_r'])} | "
                    f"{_fmt(metrics['configured_r_per_trade'])} |"
                )
        lines.append("")
    filters = payload["pine_filters"]
    lines += [
        "## Pine source check",
        "",
        f"Conclusion: **{filters['conclusion']}**. The JSON records matching source lines.",
        "",
        "## Interpretation",
        "",
        "Configured cost is divided by each trade's stop risk. Small stops therefore create large "
        "cost/R values; the distribution and filtered net-R tables determine whether that scale "
        "effect explains the loss. The 2,700-strategy comparison is only conclusive when its "
        "output includes the same risk_points and cost columns; otherwise it is recorded as "
        "missing rather than inferred.",
        "",
        "## Safety",
        "",
        json.dumps(payload["safety"], sort_keys=True),
    ]
    return "\n".join(lines) + "\n"


def _stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument(
        "--owner-trades",
        type=Path,
        default=Path("docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv"),
    )
    parser.add_argument(
        "--pine", type=Path, default=Path("docs/evidence/owner/tai_smart_filter_pine_source.txt")
    )
    parser.add_argument("--config", type=Path, default=Path("config/strategy_lab.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=DEFAULT_END.isoformat())
    parser.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START.isoformat())
    args = parser.parse_args()
    payload = run(
        args.lake,
        args.owner_trades,
        args.pine,
        args.config,
        _stamp(args.start),
        _stamp(args.end),
        _stamp(args.holdout_start),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report(payload), encoding="utf-8")
    print(
        json.dumps({"task": "T-201", "cells": len(payload["cells"]), "safety": payload["safety"]})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
