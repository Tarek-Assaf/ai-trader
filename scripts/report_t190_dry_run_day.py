#!/usr/bin/env python3
"""Read-only T-190 Tai dry-run shadow-trade report."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import sys
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    pq = None
from trader.execution.brokers.base import Direction
from trader.experiments.tai_demo import ClosedBar
from trader.experiments.tai_wiring import TaiMinuteBar, TaiSignalProvider, ThreeMinuteBarBuilder

LABEL = "Tai dry-run shadow, not real orders"


@dataclass(frozen=True)
class Minute:
    at: datetime
    epic: str
    bo: Decimal
    bh: Decimal
    bl: Decimal
    bc: Decimal
    ao: Decimal
    ah: Decimal
    al: Decimal
    ac: Decimal

    def mid(self) -> TaiMinuteBar:
        return TaiMinuteBar(
            self.at,
            self.epic,
            (self.bo + self.ao) / 2,
            (self.bh + self.ah) / 2,
            (self.bl + self.al) / 2,
            (self.bc + self.ac) / 2,
        )


@dataclass(frozen=True)
class ShadowBar:
    bar: ClosedBar
    bh: Decimal
    bl: Decimal
    bc: Decimal
    ah: Decimal
    al: Decimal
    ac: Decimal


@dataclass
class Trade:
    entry_at: datetime
    direction: Direction
    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, Decimal, Decimal]
    size: Decimal
    remaining: Decimal
    risk: Decimal
    hit: list[int] = field(default_factory=list)
    exit_at: datetime | None = None
    exit_price: Decimal | None = None
    reason: str = "open"
    ambiguous: bool = False
    points: Decimal = Decimal("0")
    usd: Decimal | None = None


def stamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result.astimezone(UTC)


def utc(value: object) -> datetime:
    result = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result.astimezone(UTC).replace(second=0, microsecond=0)


def lake_paths(root: Path, epic: str) -> tuple[Path, ...]:
    aliases = (
        (epic, "NSXUSD", "NAS100", "US100") if epic in {"NSXUSD", "NAS100", "US100"} else (epic,)
    )
    paths = [p for alias in aliases for p in (root / alias / "1m_capital").rglob("*.parquet")]
    return tuple(sorted(set(paths))) or tuple(sorted(root.glob("*/1m_capital/*/*.parquet")))


def read_minutes(root: Path, epic: str, before: datetime) -> tuple[Minute, ...]:
    if pq is None:
        raise RuntimeError("pyarrow is required to read the recorder lake")
    paired: dict[datetime, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for path in lake_paths(root, epic):
        try:
            rows = pq.read_table(
                path, columns=["epic", "ts_utc", "side", "open", "high", "low", "close"]
            ).to_pylist()
        except (OSError, ValueError, KeyError):
            continue
        for row in rows:
            try:
                at = utc(row["ts_utc"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if at >= before or row.get("side") not in {"bid", "ask"}:
                continue
            if row.get("epic") and row["epic"] not in {epic, "NSXUSD", "NAS100", "US100"}:
                continue
            paired[at][str(row["side"])] = row
    result: list[Minute] = []
    for at, sides in sorted(paired.items()):
        bid, ask = sides.get("bid"), sides.get("ask")
        if bid is None or ask is None:
            continue
        try:
            b = tuple(Decimal(str(bid[x])) for x in ("open", "high", "low", "close"))
            a = tuple(Decimal(str(ask[x])) for x in ("open", "high", "low", "close"))
            result.append(Minute(at, epic, *b, *a))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    return tuple(result)


def shadow_bars(minutes: tuple[Minute, ...], epic: str) -> tuple[ShadowBar, ...]:
    groups: dict[datetime, list[Minute]] = defaultdict(list)
    for minute in minutes:
        groups[minute.at - timedelta(minutes=minute.at.minute % 3)].append(minute)
    builder, result = ThreeMinuteBarBuilder(epic), []
    for bucket, members in sorted(groups.items()):
        members.sort(key=lambda x: x.at)
        if len(members) != 3 or [x.at for x in members] != [
            bucket + timedelta(minutes=i) for i in range(3)
        ]:
            continue
        bar = None
        for member in members:
            bar = builder.add(member.mid()) or bar
        if bar is None:
            continue
        result.append(
            ShadowBar(
                bar,
                max(x.bh for x in members),
                min(x.bl for x in members),
                members[-1].bc,
                max(x.ah for x in members),
                min(x.al for x in members),
                members[-1].ac,
            )
        )
    return tuple(result)


def side_points(direction: Direction, entry: Decimal, price: Decimal) -> Decimal:
    return price - entry if direction is Direction.BUY else entry - price


def size_for(balance: Decimal, start: Decimal, step: Decimal, minimum: Decimal) -> Decimal:
    raw = max(Decimal("0.1"), Decimal("0.1") * balance / start)
    return max(minimum, (raw / step).to_integral_value(rounding=ROUND_DOWN) * step)


def simulate(
    bars: tuple[ShadowBar, ...],
    start: datetime,
    end: datetime,
    balance: Decimal,
    step: Decimal,
    minimum: Decimal,
    vpp: Decimal | None,
) -> tuple[list[Trade], list[dict[str, str]]]:
    provider, trades, signals, current = TaiSignalProvider(minimum_history=300), [], [], None
    for item in bars:
        signal = provider(item.bar)
        if item.bar.at < start:
            continue
        if item.bar.at >= end:
            break
        if current is not None:
            stop = (
                item.bl <= current.stop
                if current.direction is Direction.BUY
                else item.ah >= current.stop
            )
            hits = [
                i
                for i, t in enumerate(current.targets, 1)
                if i not in current.hit
                and (item.ah >= t if current.direction is Direction.BUY else item.bl <= t)
            ]
            if stop and hits:
                current.ambiguous = True
            if stop:
                current.points += (
                    side_points(current.direction, current.entry, current.stop) * current.remaining
                )
                if vpp is not None:
                    current.usd = (current.usd or Decimal("0")) + side_points(
                        current.direction, current.entry, current.stop
                    ) * current.remaining * vpp
                current.exit_at, current.exit_price, current.reason = (
                    item.bar.at,
                    current.stop,
                    "stop",
                )
                trades.append(current)
                current = None
            else:
                for i in hits:
                    current.hit.append(i)
                    amount = (
                        current.size * (Decimal("0.4"), Decimal("0.3"), Decimal("0.3"))[i - 1]
                        if i < 3
                        else current.remaining
                    )
                    price = current.targets[i - 1]
                    current.points += side_points(current.direction, current.entry, price) * amount
                    if vpp is not None:
                        current.usd = (current.usd or Decimal("0")) + side_points(
                            current.direction, current.entry, price
                        ) * amount * vpp
                    current.remaining -= amount
                    if i == 1:
                        current.stop = current.entry
                    if i == 3 or current.remaining <= 0:
                        current.exit_at, current.exit_price, current.reason = (
                            item.bar.at,
                            price,
                            f"target_{i}",
                        )
                        trades.append(current)
                        current = None
                        break
        if current is None and signal is not None:
            signals.append(
                {
                    "at": item.bar.at.isoformat(),
                    "direction": signal.direction.value,
                    "stop": str(signal.stop),
                }
            )
            entry = item.ac if signal.direction is Direction.BUY else item.bc
            sz = size_for(balance, balance, step, minimum)
            current = Trade(
                item.bar.at,
                signal.direction,
                entry,
                signal.stop,
                signal.levels(),
                sz,
                sz,
                abs(signal.entry - signal.stop),
            )
    if current is not None:
        current.exit_at, current.exit_price, current.reason = end, current.entry, "window_end"
        trades.append(current)
    return trades, signals


def journal_lines(path: Path | None, start: datetime, end: datetime) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("event") != "dry_run_would_submit":
            continue
        value = item.get("at") or item.get("timestamp") or item.get("time")
        if not value:
            continue
        try:
            at = stamp(str(value))
        except ValueError:
            continue
        if start <= at < end:
            result.append({"at": at.isoformat(), "direction": str(item.get("direction", ""))})
    return result


def payload(trade: Trade, vpp: Decimal | None) -> dict[str, Any]:
    return {
        "entry_time": trade.entry_at.isoformat(),
        "exit_time": trade.exit_at.isoformat() if trade.exit_at else None,
        "direction": trade.direction.value,
        "entry": str(trade.entry),
        "stop": str(trade.stop),
        "targets_hit": trade.hit,
        "exit_price": str(trade.exit_price),
        "points": str(trade.points),
        "usd": str(trade.usd) if vpp is not None else None,
        "r_multiple": str(trade.points / trade.risk if trade.risk else Decimal("0")),
        "size": str(trade.size),
        "reason": trade.reason,
        "both_stop_and_target_touched": trade.ambiguous,
    }


def write_outputs(
    out: Path,
    trades: list[Trade],
    signals: list[dict[str, str]],
    journal: list[dict[str, str]],
    start: datetime,
    end: datetime,
    gaps: int,
    vpp: Decimal | None,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows = [payload(t, vpp) for t in trades]
    net = sum((t.points for t in trades), Decimal("0"))
    usd = sum((t.usd or Decimal("0") for t in trades), Decimal("0")) if vpp is not None else None
    matched = sum(
        1
        for s in signals
        if any(s["at"] == j["at"] and s["direction"] == j["direction"] for j in journal)
    )
    summary = {
        "task": "T-190",
        "label": LABEL,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "trades": rows,
        "trade_count": len(rows),
        "net_points": str(net),
        "net_usd": str(usd) if usd is not None else None,
        "cross_check": {
            "shadow_signals": len(signals),
            "journal_signals": len(journal),
            "matched": matched,
            "agrees": matched == len(signals) == len(journal),
        },
        "data_gaps_minutes": gaps,
        "safety": {
            "broker_calls": 0,
            "order_calls": 0,
            "network_access": False,
            "sealed_holdout_read": False,
            "lake_writes": False,
            "risk_or_gate_edits": False,
        },
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    fields = (
        list(rows[0])
        if rows
        else [
            "entry_time",
            "exit_time",
            "direction",
            "entry",
            "stop",
            "targets_hit",
            "exit_price",
            "points",
            "usd",
            "r_multiple",
            "size",
            "reason",
            "both_stop_and_target_touched",
        ]
    )
    with (out / "trades.csv").open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    net_value = usd if usd is not None else "USD unavailable (value_per_point not recorded)"
    verdict = (
        f"{len(rows)} shadow trades, net {net_value} USD ({net} points), "
        f"cross-check agrees {matched} of {len(signals)} signals; data gaps {gaps} minutes."
    )
    report = [
        f"# T-190 dry-run day report — {LABEL}",
        "",
        verdict,
        "",
        f"Window: {start.isoformat()} through {end.isoformat()}.",
        "Fills use recorded ask for BUY and bid for SELL; no spread or slippage cost is invented.",
        "USD is shown only with explicit --value-per-point.",
        "",
        "| Entry | Direction | Entry | Stop | Targets | Exit | Points | USD | R | Reason |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|---|",
    ]
    report += [
        f"| {r['entry_time']} | {r['direction']} | {r['entry']} | {r['stop']} | "
        f"{r['targets_hit']} | {r['exit_price']} | {r['points']} | "
        f"{r['usd'] or 'n/a'} | {r['r_multiple']} | {r['reason']} |"
        for r in rows
    ]
    report += [
        "",
        f"Cross-check: {matched} of {len(signals)} shadow signals agree with "
        f"{len(journal)} dry_run_would_submit lines.",
        f"Data gaps: {gaps} missing minutes.",
        "",
        "This is a dry-run shadow report, not real orders.",
    ]
    (out / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    try:
        import matplotlib.pyplot as plt  # type: ignore[import-not-found]

        eq = [0.0]
        total = 0.0
        for t in trades:
            total += float(t.usd or 0)
            eq.append(total)
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(range(len(eq)), eq, marker="o")
        ax.set_xlabel("Closed trade")
        ax.set_ylabel("Equity (USD)")
        ax.set_title("Tai dry-run shadow equity")
        fig.tight_layout()
        fig.savefig(out / "equity.png", dpi=120)
        plt.close(fig)
    except Exception:
        (out / "equity.png").write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lake", type=Path, required=True)
    p.add_argument("--epic", default="NSXUSD")
    p.add_argument("--from", dest="start", type=stamp, required=True)
    p.add_argument("--to", dest="end", type=stamp, required=True)
    p.add_argument("--start-balance", type=Decimal, required=True)
    p.add_argument("--output-dir", type=Path, default=Path("docs/evidence/t190"))
    p.add_argument("--journal", type=Path)
    p.add_argument("--value-per-point", type=Decimal)
    args = p.parse_args()
    if args.end <= args.start or args.start_balance <= 0:
        p.error("invalid window or balance")
    minutes = read_minutes(args.lake, args.epic, args.end)
    bars = shadow_bars(minutes, args.epic)
    trades, signals = simulate(
        bars,
        args.start,
        args.end,
        args.start_balance,
        Decimal("0.1"),
        Decimal("0.1"),
        args.value_per_point,
    )
    journal = journal_lines(args.journal, args.start, args.end)
    stamps = {m.at for m in minutes if args.start <= m.at < args.end}
    gaps = (
        max(0, int((max(stamps) - min(stamps)).total_seconds() / 60 + 1) - len(stamps))
        if stamps
        else 0
    )
    write_outputs(
        args.output_dir, trades, signals, journal, args.start, args.end, gaps, args.value_per_point
    )
    print(
        json.dumps(
            {
                "task": "T-190",
                "label": LABEL,
                "trades": len(trades),
                "signals": len(signals),
                "journal_signals": len(journal),
                "output": str(args.output_dir),
                "safety": {
                    "broker_calls": 0,
                    "order_calls": 0,
                    "network_access": False,
                    "sealed_holdout_read": False,
                    "lake_writes": False,
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
