#!/usr/bin/env python3
"""Build and dry-check the bounded historical Tai port harness.

The harness imports the unchanged T-162 port and only replays the owner window.
It refuses a window that reaches the sealed-holdout boundary before touching the
lake.  It is diagnostic-only: no broker, order, holdout, lake-write, tuning or
secret path exists here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts import lab_t162_pine_port as port

    from trader.lab.runner import _path_month, pessimistic_cost_model
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import lab_t162_pine_port as port

    from trader.lab.runner import _path_month, pessimistic_cost_model


NY = ZoneInfo("America/New_York")
INSTRUMENTS = ("NSXUSD", "XAUUSD")
TIMEFRAMES = (1, 3, 5, 60)
LABEL = (
    "port of the stored indicator, loosely matched to the owner "
    "(3/31 within 3 bars, 15/31 within 30 bars)"
)
DEFAULT_START = datetime(2026, 10, 6, tzinfo=NY).astimezone(UTC)
DEFAULT_END = datetime(2026, 10, 17, tzinfo=NY).astimezone(UTC)
DEFAULT_HOLDOUT_START = DEFAULT_END
WARMUP = timedelta(days=7)

# The dry check runs four chart timeframes per instrument.  Cache the immutable
# 1-minute source once per instrument so each cell only filters and resamples
# the already loaded rows; no data is written to the lake.
_SOURCE_CACHE: dict[Path, dict[str, tuple[Any, ...]]] = {}


def _source_files(lake: Path, instrument: str) -> tuple[Path, ...]:
    roots = [lake / instrument / "1m", lake / instrument / "1m_capital"]
    if instrument == "NSXUSD":
        roots.extend(
            lake / symbol / timeframe
            for symbol, timeframe in (
                ("NAS100", "1m_capital"),
                ("NAS100", "1m"),
                ("US100", "1m"),
            )
        )
    return tuple(sorted({path for root in roots for path in root.glob("**/*.parquet")}))


def parse_date(value: str) -> datetime:
    """Parse an owner-window date as New York midnight, returned in UTC."""
    try:
        local = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=NY)
    except ValueError as exc:
        raise ValueError(f"date must be YYYY-MM-DD: {value}") from exc
    return local.astimezone(UTC)


def parse_stamp(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def guard_window(start: datetime, end: datetime, holdout_start: datetime) -> None:
    """Reject a window touching the sealed holdout before reading any data."""
    if end <= start:
        raise ValueError("window end must be after window start")
    if end > holdout_start:
        raise ValueError(
            "requested window touches the sealed holdout; historical harness refuses it"
        )


def _load_window_source(
    lake: Path, instrument: str, start: datetime, end: datetime
) -> tuple[Any, ...]:
    """Read only owner-window month files into the unchanged Candle type."""
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - project image supplies pyarrow
        raise RuntimeError("pyarrow is required for Strategy Lab data") from exc
    rows: dict[datetime, Any] = {}
    for path in _source_files(lake, instrument):
        month = _path_month(path)
        if month is not None:
            month_start = datetime(month[0], month[1], 1, tzinfo=UTC)
            next_year = month[0] + (1 if month[1] == 12 else 0)
            next_month = 1 if month[1] == 12 else month[1] + 1
            month_end = datetime(next_year, next_month, 1, tzinfo=UTC)
            if month_start >= end or month_end <= start:
                continue
        try:
            available = set(pq.ParquetFile(path).schema.names)
            required = {"ts_utc", "open", "high", "low", "close"}
            if not required.issubset(available):
                continue
            names = sorted(required | ({"volume"} if "volume" in available else set()))
            table = pq.read_table(path, columns=names)
            for row in table.to_pylist():
                stamp = datetime.fromisoformat(str(row["ts_utc"]).replace("Z", "+00:00"))
                if stamp.tzinfo is None or stamp.utcoffset() is None:
                    stamp = stamp.replace(tzinfo=UTC)
                stamp = stamp.astimezone(UTC)
                if not start <= stamp < end:
                    continue
                values = tuple(float(row[name] or 0.0) for name in ("open", "high", "low", "close"))
                volume = float(row.get("volume") or 0.0)
                rows[stamp] = port.Candle(stamp, *values, volume, (instrument, "histdata"), True, 1)
        except (OSError, KeyError, TypeError, ValueError, OverflowError):
            continue
    return tuple(rows[key] for key in sorted(rows))


def _bars_for_window(
    lake: Path, instrument: str, timeframe: int, start: datetime, end: datetime
) -> tuple[Any, ...]:
    lake_key = lake.resolve()
    source_by_instrument = _SOURCE_CACHE.setdefault(lake_key, {})
    source = source_by_instrument.get(instrument)
    if source is None:
        source = _load_window_source(lake, instrument, start - WARMUP, end)
        source_by_instrument[instrument] = source
    bars = tuple(item for item in port.resample_ohlc(source, timeframe) if item.complete)
    return tuple(item for item in bars if start - WARMUP <= item.ts_utc < end)


def _replay(bars: tuple[Any, ...], timeframe: int) -> tuple[tuple[Any, ...], Any]:
    """Run the unchanged port with its chart timeframe selected dynamically."""
    old_timeframe, old_higher = port.TIMEFRAME, port.HIGHER_MINUTES
    port.TIMEFRAME = timeframe
    port.HIGHER_MINUTES = timeframe * 18
    try:
        return port.replay(bars, "closed")
    finally:
        port.TIMEFRAME, port.HIGHER_MINUTES = old_timeframe, old_higher


def _in_window(stamp: datetime, start: datetime, end: datetime) -> bool:
    return start <= stamp < end


def _signal_count(features: Any, bars: tuple[Any, ...], start: datetime, end: datetime) -> int:
    """Count port signals only for chart bars inside the requested owner window."""
    return sum(
        flag
        for bar, flag in zip(bars, features.buy_cross, strict=True)
        if _in_window(bar.ts_utc, start, end)
    ) + sum(
        flag
        for bar, flag in zip(bars, features.sell_cross, strict=True)
        if _in_window(bar.ts_utc, start, end)
    )


def _gross_r(trade: Any) -> float | None:
    if trade.exit_reason is None:
        return None
    if trade.exit_reason == "tp3":
        return 3.0
    if trade.exit_reason == "be":
        return 0.0
    return -1.0


def _trade_payload(trade: Any, instrument: str, costs: Any) -> dict[str, Any]:
    risk = abs(float(trade.entry_price) - float(trade.stop_price))
    spread, slippage = costs.for_bar(instrument, trade.entry_time)
    cost_points = spread + 2.0 * slippage
    gross_r = _gross_r(trade)
    net_r = None if gross_r is None or risk <= 0 else gross_r - cost_points / risk
    return {
        "entry": trade.entry_time.isoformat(),
        "exit": trade.exit_time.isoformat() if trade.exit_time else None,
        "side": trade.direction,
        "entry_price": trade.entry_price,
        "stop_price": trade.stop_price,
        "risk_points": risk,
        "exit_reason": trade.exit_reason,
        "gross_r": gross_r,
        "r": net_r,
        "spread_points": spread,
        "slippage_points": slippage,
        "cost_points": cost_points,
    }


def _year_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade["entry"][:4])].append(trade)
    return {
        year: {
            "trades": len(items),
            "completed": sum(item["exit"] is not None for item in items),
            "net_r": sum(item["r"] or 0.0 for item in items),
        }
        for year, items in sorted(grouped.items())
    }


def run_cell(
    lake: Path,
    instrument: str,
    timeframe: int,
    start: datetime,
    end: datetime,
    costs: Any,
) -> dict[str, Any]:
    bars = _bars_for_window(lake, instrument, timeframe, start, end)
    if not bars:
        return {
            "instrument": instrument,
            "timeframe_minutes": timeframe,
            "source_rows": 0,
            "bars": 0,
            "warmup_bars": 0,
            "window_bars": 0,
            "signal_count": 0,
            "trades": [],
            "per_year": {},
            "status": "no rows in owner window",
            "label": LABEL,
        }
    warmup_bars = tuple(bar for bar in bars if bar.ts_utc < start)
    window_bars = tuple(bar for bar in bars if _in_window(bar.ts_utc, start, end))
    raw_trades, features = _replay(bars, timeframe)
    trades = [
        _trade_payload(item, instrument, costs)
        for item in raw_trades
        if _in_window(item.entry_time, start, end)
    ]
    discarded_warmup_trades = sum(item.entry_time < start for item in raw_trades)
    signal_count = _signal_count(features, bars, start, end)
    zero_trade_reason = "not exposed" if signal_count > 0 and not trades else None
    return {
        "instrument": instrument,
        "timeframe_minutes": timeframe,
        "source_rows": len(window_bars),
        "bars": len(window_bars),
        "warmup_bars": len(warmup_bars),
        "window_bars": len(window_bars),
        "signal_count": signal_count,
        "trade_count": len(trades),
        "discarded_warmup_trades": discarded_warmup_trades,
        "zero_trade_reason": zero_trade_reason,
        "trades": trades,
        "per_year": _year_summary(trades),
        "status": "ok",
        "label": LABEL,
    }


def run(
    lake: Path,
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
    holdout_start: datetime = DEFAULT_HOLDOUT_START,
    config: Path = Path("config/strategy_lab.yaml"),
) -> dict[str, Any]:
    guard_window(start, end, holdout_start)
    costs = pessimistic_cost_model(config)
    cells = [
        run_cell(lake, instrument, timeframe, start, end, costs)
        for instrument in INSTRUMENTS
        for timeframe in TIMEFRAMES
    ]
    return {
        "task": "T-181",
        "generated_at": datetime.now(UTC).isoformat(),
        "label": LABEL,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "holdout_guard": {
            "tested": True,
            "holdout_start": holdout_start.isoformat(),
            "longer_history_run": False,
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


def report(payload: dict[str, Any]) -> str:
    cells = payload["cells"]
    lines = [
        "# T-181 historical Tai harness window-only dry check",
        "",
        f"**Verdict:** Window-only counts yes; cells run "
        f"{sum(c['status'] == 'ok' for c in cells)}/8; "
        f"zero-trade cells explained {sum(c.get('zero_trade_reason') is not None for c in cells)}; "
        "longer history NOT run.",
        "",
        f"Owner window: `{payload['window']['start']}` through `{payload['window']['end']}` "
        "(end exclusive; New York midnight dates).",
        "",
        f"Label: `{LABEL}`",
        "",
        "| Instrument | Timeframe | Warmup bars | Window bars | Signals | Trades | Status |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for cell in cells:
        lines.append(
            f"| {cell['instrument']} | {cell['timeframe_minutes']}m | "
            f"{cell.get('warmup_bars', 0)} | {cell.get('window_bars', cell['bars'])} | "
            f"{cell['signal_count']} | "
            f"{cell.get('trade_count', 0)} | {cell['status']} |"
        )
    lines += [
        "",
        "The harness replays the unchanged T-162 port with seven days of warm-up, "
        "uses the configured pessimistic spread/slippage model, and emits entry/exit/side/R "
        "trade rows and per-year summaries. XAUUSD is reported plainly when the lake has no rows.",
        "",
        "## Safety",
        "",
        json.dumps(payload["safety"], sort_keys=True),
        "",
        "Zero-trade cells report `not exposed` because the unchanged T-162 port "
        "does not expose entry-filter counters.",
        "",
        "Longer history was not run; this is a dry check of the owner window only.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--start", default="2026-10-06")
    parser.add_argument("--end", default="2026-10-17")
    parser.add_argument("--holdout-start", default="2026-10-17T04:00:00+00:00")
    parser.add_argument("--config", type=Path, default=Path("config/strategy_lab.yaml"))
    args = parser.parse_args()
    start = parse_date(args.start)
    end = parse_date(args.end)
    holdout_start = parse_stamp(args.holdout_start)
    payload = run(args.lake, start, end, holdout_start, args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "task": "T-181",
                "cells": len(payload["cells"]),
                "safety": payload["safety"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
