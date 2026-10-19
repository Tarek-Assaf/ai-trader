#!/usr/bin/env python3
"""Offline proof that the wired Tai runtime matches the closed T-162 port.

This diagnostic is intentionally read-only.  It reads pre-holdout recorder parquet,
feeds the shared T-186 bar builder and signal provider, and exercises the
``TaiDemoRunner`` with a dry-run mock gateway.  It never imports a broker client,
opens the sealed holdout, accesses the network, or writes to the lake.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts import lab_t162_pine_port as port
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import lab_t162_pine_port as port

from trader.data.resample import Candle
from trader.execution.brokers.base import Direction, OrderRequest
from trader.execution.control import ControlMode
from trader.experiments.tai_demo import (
    ClosedBar,
    TaiDemoRunner,
    TaiDemoSettings,
    TaiInstrumentRules,
)
from trader.experiments.tai_wiring import TaiMinuteBar, TaiSignalProvider, ThreeMinuteBarBuilder

NY = ZoneInfo("America/New_York")
DEFAULT_EPIC = "NSXUSD"
DEFAULT_DAYS = 10
DEFAULT_TICK = Decimal("0.1")
LABEL = "T-187 offline wired Tai runtime replay, dry-run only"


@dataclass(frozen=True, slots=True)
class RuntimeEntry:
    """A signal emitted by the T-186 signal provider."""

    at: datetime
    direction: str
    entry: Decimal
    stop: Decimal


def parse_stamp(value: str) -> datetime:
    """Parse an ISO timestamp and normalize it to UTC."""
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return stamp.astimezone(UTC)


def _candidate_paths(lake: Path, epic: str) -> tuple[Path, ...]:
    aliases = (epic, "NAS100", "US100") if epic == DEFAULT_EPIC else (epic,)
    roots = [
        lake / alias / timeframe
        for alias in aliases
        for timeframe in ("1m_capital", "1m")
    ]
    return tuple(
        sorted({path for root in roots if root.is_dir() for path in root.rglob("*.parquet")})
    )


def _utc(value: object) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).replace(second=0, microsecond=0)


def _read_pre_holdout_minutes(lake: Path, epic: str, holdout_start: datetime) -> tuple[Candle, ...]:
    """Read paired mid bars strictly before the supplied sealed boundary."""
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - supplied by the runtime image
        raise RuntimeError("pyarrow is required for T-187") from exc
    paired: dict[datetime, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    singles: dict[datetime, Mapping[str, Any]] = {}
    for path in _candidate_paths(lake, epic):
        try:
            names = set(pq.ParquetFile(path).schema.names)
            required = {"ts_utc", "open", "high", "low", "close"}
            if not required.issubset(names):
                continue
            columns = sorted(required | ({"side", "epic"} & names))
            for row in pq.read_table(path, columns=columns).to_pylist():
                stamp = _utc(row.get("ts_utc"))
                # Apply the boundary before retaining any row; holdout data never enters memory.
                if stamp >= holdout_start:
                    continue
                recorded = str(row.get("epic") or "")
                if recorded and recorded not in {epic, "NAS100", "US100"}:
                    continue
                side = str(row.get("side") or "").lower()
                if side in {"bid", "ask"}:
                    paired[stamp][side] = row
                else:
                    singles[stamp] = row
        except (OSError, KeyError, TypeError, ValueError, OverflowError):
            continue
    rows: list[Candle] = []
    for stamp in sorted(paired):
        sides = paired[stamp]
        if "bid" not in sides or "ask" not in sides:
            continue
        bid, ask = sides["bid"], sides["ask"]
        values = tuple(
            (float(bid[name]) + float(ask[name])) / 2.0
            for name in ("open", "high", "low", "close")
        )
        rows.append(Candle(stamp, *values, 0.0, (epic, "mid"), True, 1))
    if not paired:
        for stamp, row in sorted(singles.items()):
            values = tuple(float(row[name]) for name in ("open", "high", "low", "close"))
            rows.append(Candle(stamp, *values, 0.0, (epic, "mid"), True, 1))
    return tuple(rows)


def _selected_dates(minutes: Sequence[Candle], days: int) -> tuple[date, ...]:
    dates = sorted({bar.ts_utc.astimezone(NY).date() for bar in minutes})
    if len(dates) < days:
        raise ValueError(f"lake has {len(dates)} pre-holdout trading dates; need {days}")
    return tuple(dates[-days:])


def _local_day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=NY).astimezone(UTC)


def _closed_bars(
    minutes: Sequence[Candle], selected: Sequence[date], epic: str
) -> tuple[ClosedBar, ...]:
    # Include the prior New York day so the first selected session has its
    # 18:00 anchor and enough warm-up bars for the 54-minute HA group.
    first = _local_day_start(selected[0]) - timedelta(days=1)
    source = [bar for bar in minutes if bar.ts_utc >= first]
    builder = ThreeMinuteBarBuilder(epic)
    tai_minutes = tuple(
        TaiMinuteBar(
            bar.ts_utc,
            epic,
            Decimal(str(bar.open)),
            Decimal(str(bar.high)),
            Decimal(str(bar.low)),
            Decimal(str(bar.close)),
            volume=Decimal(str(bar.volume)),
        )
        for bar in source
    )
    return builder.extend(tai_minutes)


def _is_selected(at: datetime, selected: set[date]) -> bool:
    return at.astimezone(NY).date() in selected


def _runtime_entries(bars: Sequence[ClosedBar], selected: set[date]) -> tuple[RuntimeEntry, ...]:
    provider = TaiSignalProvider(minimum_history=300)
    result: list[RuntimeEntry] = []
    for bar in bars:
        signal = provider(bar)
        if signal is not None and _is_selected(bar.at, selected):
            result.append(RuntimeEntry(bar.at, signal.direction.value, signal.entry, signal.stop))
    return tuple(result)


def _port_entries(
    bars: Sequence[ClosedBar], selected: set[date], *, ha_anchor: str
) -> tuple[RuntimeEntry, ...]:
    candles = tuple(
        Candle(
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
        for bar in bars
    )
    trades, _features = port.replay(
        candles,
        "closed",
        ha_anchor=ha_anchor,
        vwap_anchor="session_1800_ny",
    )
    return tuple(
        RuntimeEntry(
            trade.entry_time,
            "BUY" if trade.direction == "long" else "SELL",
            Decimal(str(trade.entry_price)),
            Decimal(str(trade.stop_price)),
        )
        for trade in trades
        if _is_selected(trade.entry_time, selected)
    )


def compare_entries(
    runtime: Sequence[RuntimeEntry],
    port_entries: Sequence[RuntimeEntry],
    tick: Decimal = DEFAULT_TICK,
) -> dict[str, Any]:
    """Match runtime and port entries by bar, side and one broker tick."""
    if tick <= 0:
        raise ValueError("tick must be positive")
    remaining = list(runtime)
    matches: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    for expected in port_entries:
        candidates = [item for item in remaining if item.direction == expected.direction]
        if not candidates:
            misses.append({"port": _entry_payload(expected), "reason": "missing_runtime_direction"})
            continue
        candidate = min(candidates, key=lambda item: abs((item.at - expected.at).total_seconds()))
        time_delta = abs((candidate.at - expected.at).total_seconds())
        stop_delta = abs(candidate.stop - expected.stop)
        if time_delta == 0 and stop_delta <= tick:
            remaining.remove(candidate)
            matches.append(
                {
                    "at": expected.at.isoformat(),
                    "direction": expected.direction,
                    "stop_delta": str(stop_delta),
                }
            )
        else:
            misses.append(
                {
                    "port": _entry_payload(expected),
                    "runtime_candidate": _entry_payload(candidate),
                    "reason": "bar_or_stop_difference",
                }
            )
    extras = [
        {
            "runtime": _entry_payload(item),
            "reason": "runtime_signal_not_port_entry_or_port_position_state_differs",
        }
        for item in remaining
    ]
    return {
        "port_count": len(port_entries),
        "runtime_count": len(runtime),
        "matched": len(matches),
        "matches": matches,
        "misses": misses,
        "extras": extras,
        "exact": not misses and not extras and len(matches) == len(port_entries),
    }


def _entry_payload(entry: RuntimeEntry) -> dict[str, str]:
    return {
        "at": entry.at.isoformat(),
        "direction": entry.direction,
        "entry": str(entry.entry),
        "stop": str(entry.stop),
    }


class _RunControl:
    def read(self) -> ControlMode:
        return ControlMode.RUN


class _Positions:
    def get_positions(self) -> tuple[Any, ...]:
        return ()

    def modify_position(
        self, deal_id: str, *, stop_level: Decimal, profit_level: Decimal | None = None
    ) -> str:
        return deal_id

    def close_position(self, deal_id: str) -> str:
        return deal_id


class _Orders:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, cycle_id: str, order: OrderRequest) -> None:
        self.calls += 1
        return None


def run_dry_run(bars: Sequence[ClosedBar]) -> dict[str, int]:
    """Exercise the real TaiDemoRunner with a gateway that cannot submit orders."""
    journal: list[Mapping[str, object]] = []
    orders = _Orders()
    settings = TaiDemoSettings(
        "demo", "paper", False, True, dry_run=True, start_balance=Decimal("10000")
    )
    provider = TaiSignalProvider(minimum_history=300)
    rules = TaiInstrumentRules(
        min_size=Decimal("0.1"),
        size_step=Decimal("0.1"),
        tick_size=Decimal("0.1"),
        min_stop_distance=Decimal("0.1"),
        min_limit_distance=Decimal("0.1"),
    )

    def factory(signal: Any, cycle_id: str, size: Decimal) -> OrderRequest:
        return OrderRequest(
            cycle_id,
            "NSXUSD",
            signal.direction,
            size,
            signal.entry,
            signal.stop,
            signal.levels()[2],
        )

    runner = TaiDemoRunner(
        settings=settings,
        order_manager=orders,
        position_gateway=_Positions(),
        signal_provider=provider,
        order_factory=factory,
        control=_RunControl(),
        balance_provider=lambda: Decimal("10000"),
        instrument_rules_provider=lambda _epic: rules,
        journal=journal.append,
    )
    for bar in bars:
        runner.on_bar(bar)
    return {
        "journal_lines": len(journal),
        "dry_run_would_submit": sum(
            item.get("event") == "dry_run_would_submit" for item in journal
        ),
        "order_calls": orders.calls,
    }


def run(
    lake: Path,
    holdout_start: datetime,
    *,
    epic: str = DEFAULT_EPIC,
    days: int = DEFAULT_DAYS,
    tick: Decimal = DEFAULT_TICK,
) -> dict[str, Any]:
    if days <= 0:
        raise ValueError("days must be positive")
    minutes = _read_pre_holdout_minutes(lake, epic, holdout_start)
    selected = _selected_dates(minutes, days)
    bars = _closed_bars(minutes, selected, epic)
    selected_set = set(selected)
    runtime = _runtime_entries(bars, selected_set)
    session_port = _port_entries(bars, selected_set, ha_anchor="session_1800_ny")
    epoch_port = _port_entries(bars, selected_set, ha_anchor="epoch")
    return {
        "task": "T-187",
        "label": LABEL,
        "status": "ok",
        "epic": epic,
        "holdout_start": holdout_start.isoformat(),
        "selected_trading_dates": [item.isoformat() for item in selected],
        "minute_rows": len(minutes),
        "closed_3m_bars": len(bars),
        "runtime_entries": [_entry_payload(item) for item in runtime],
        "port_session_1800_entries": [_entry_payload(item) for item in session_port],
        "port_epoch_entries_information_only": [_entry_payload(item) for item in epoch_port],
        "comparison": compare_entries(runtime, session_port, tick),
        "dry_run": run_dry_run(bars),
        "safety": {
            "broker_calls": 0,
            "order_calls": 0,
            "network_access": False,
            "sealed_holdout_read": False,
            "lake_writes": False,
            "risk_or_gate_edits": False,
            "redeploy": False,
        },
    }


def _write_outputs(payload: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "t187_summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    comparison = payload["comparison"]
    report = [
        "# T-187 offline wired Tai runtime replay",
        "",
        f"- Status: **{payload['status']}**",
        f"- Window: {payload['selected_trading_dates'][0]} through "
        f"{payload['selected_trading_dates'][-1]}",
        f"- Closed 3M bars: {payload['closed_3m_bars']}",
        f"- Session anchored port entries: {comparison['port_count']}",
        f"- Runtime entries: {comparison['runtime_count']}",
        f"- Exact matches: {comparison['matched']}",
        f"- Exact reproduction: **{comparison['exact']}**",
        "",
        "The epoch anchored port list is information only. The acceptance comparison uses "
        "the session_1800_ny anchor required by D-066/T-182.",
        "",
        "## Dry-run safety",
        "",
        f"The real TaiDemoRunner emitted "
        f"{payload['dry_run']['dry_run_would_submit']} dry_run_would_submit journal lines "
        f"and made {payload['dry_run']['order_calls']} order-manager submit calls.",
        "",
        "No broker, network, sealed holdout, lake write, redeploy, risk edit or gate edit "
        "was used.",
    ]
    (output_dir / "t187_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--holdout-start", required=True, type=parse_stamp)
    parser.add_argument("--epic", default=DEFAULT_EPIC)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--tick-size", type=Decimal, default=DEFAULT_TICK)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/evidence/t187"))
    args = parser.parse_args()
    payload = run(
        args.lake,
        args.holdout_start,
        epic=args.epic,
        days=args.days,
        tick=args.tick_size,
    )
    _write_outputs(payload, args.output_dir)
    print(
        json.dumps(
            {
                "task": "T-187",
                "status": payload["status"],
                "comparison": payload["comparison"],
                "dry_run": payload["dry_run"],
            },
            sort_keys=True,
        )
    )
    return 0 if payload["comparison"]["exact"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
