"""Closed-trade results read from Capital.com and kept in the bot's own audit log.

Capital.com's ``history/transactions`` reports every closed trade (or part of one) with its
real profit or loss in the ``size`` field of a "Trade closed" line, keyed by ``dealId``.  The
bot stores each line once (keyed by its ``reference``) as a ``trade_result`` audit event, so
``/trades``, ``/pnl`` and the reports can show real results and restarts never repeat a message.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.engine import Engine

from trader.core.audit import AuditEvent
from trader.core.db import tables

LOGGER = logging.getLogger(__name__)
RIYADH = ZoneInfo("Asia/Riyadh")
RESULT_EVENT = "trade_result"
FRESH_ALERT_SECONDS = 20 * 60
LOOKBACK_HOURS = 12
# Ask for history that ended a little ago: Capital.com answers error.invalid.daterange when
# the window ends even a moment after its own clock, which the VM clock sometimes does.
END_MARGIN_SECONDS = 15
_SOURCES = {
    "SL": "stop loss",
    "TP": "take profit",
    "USER": "closed by the bot or by you",
    "SYSTEM": "closed by Capital.com",
    "CLOSE_OUT": "closed out by Capital.com",
    "DEALER": "closed by Capital.com",
}


@dataclass(frozen=True, slots=True)
class ClosedLeg:
    """One "Trade closed" line: all or part of a position, with its real result."""

    reference: str
    deal_id: str
    closed_at: datetime
    epic: str
    pnl: Decimal
    currency: str


def parse_transactions(rows: Sequence[Mapping[str, Any]]) -> list[ClosedLeg]:
    """Closed-trade lines from ``history/transactions``; anything unreadable is skipped."""
    legs: list[ClosedLeg] = []
    for row in rows:
        if not isinstance(row, Mapping) or str(row.get("transactionType", "")).upper() != "TRADE":
            continue
        if "closed" not in str(row.get("note", "")).lower():
            continue
        try:
            legs.append(
                ClosedLeg(
                    reference=str(row["reference"]),
                    deal_id=str(row["dealId"]),
                    closed_at=datetime.fromisoformat(str(row["dateUtc"])).replace(tzinfo=UTC),
                    epic=str(row.get("instrumentName", "")),
                    pnl=Decimal(str(row["size"])),
                    currency=str(row.get("currency", "USD")),
                )
            )
        except (KeyError, ValueError, InvalidOperation):
            continue
    return legs


def closing_reason(leg: ClosedLeg, activities: Sequence[Mapping[str, Any]]) -> str:
    """Why the position closed, from the nearest activity line for the same deal."""
    best: tuple[float, str] | None = None
    for item in activities:
        if not isinstance(item, Mapping) or str(item.get("dealId", "")) != leg.deal_id:
            continue
        try:
            when = datetime.fromisoformat(str(item["dateUTC"])).replace(tzinfo=UTC)
        except (KeyError, ValueError):
            continue
        gap = abs((when - leg.closed_at).total_seconds())
        if gap <= 120 and (best is None or gap < best[0]):
            best = (gap, str(item.get("source", "")).upper())
    if best is None or not best[1]:
        return ""
    return _SOURCES.get(best[1], best[1].lower())


def money(value: Decimal, currency: str = "USD") -> str:
    sign = "-" if value < 0 else "+"
    symbol = "$" if currency.upper() == "USD" else f"{currency} "
    return f"{sign}{symbol}{abs(value):.2f}"


class TradeLedger:
    """Reads and writes ``trade_result`` events (and the entry events they pair with)."""

    def __init__(self, engine: Engine, writer: Any) -> None:
        self._engine = engine
        self._writer = writer

    def _events(self, event_type: str, limit: int = 1000) -> list[tuple[datetime, dict[str, Any]]]:
        table = tables["audit_log"]
        with self._engine.connect() as connection:
            records = connection.execute(
                select(table.c.created_at, table.c.payload)
                .where(table.c.event_type == event_type)
                .order_by(table.c.sequence.desc())
                .limit(limit)
            ).all()
        result: list[tuple[datetime, dict[str, Any]]] = []
        for created, raw in records:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                result.append((created, payload))
        return result

    def known_references(self) -> set[str]:
        return {str(p.get("reference")) for _, p in self._events(RESULT_EVENT)}

    def one_r_values(self) -> dict[str, Decimal]:
        """Dollar value of 1R per deal, from the entry events."""
        values: dict[str, Decimal] = {}
        for _, payload in self._events("entry"):
            deal, one_r = payload.get("deal_id"), payload.get("one_r_value")
            if deal and one_r not in (None, ""):
                try:
                    values[str(deal)] = Decimal(str(one_r))
                except InvalidOperation:
                    continue
        return values

    def record(self, leg: ClosedLeg, reason: str) -> None:
        self._writer.append(
            AuditEvent(
                RESULT_EVENT,
                {
                    "event": RESULT_EVENT,
                    "reference": leg.reference,
                    "deal_id": leg.deal_id,
                    "closed_at": leg.closed_at.isoformat(),
                    "epic": leg.epic,
                    "pnl": str(leg.pnl),
                    "currency": leg.currency,
                    "reason": reason,
                },
            )
        )

    def results_since(self, start: datetime) -> list[dict[str, Any]]:
        """Stored results closed at or after ``start`` (UTC), oldest first."""
        out: list[dict[str, Any]] = []
        for _, payload in reversed(self._events(RESULT_EVENT)):
            try:
                closed = datetime.fromisoformat(str(payload["closed_at"]))
                pnl = Decimal(str(payload["pnl"]))
            except (KeyError, ValueError, InvalidOperation):
                continue
            if closed >= start:
                out.append({**payload, "closed_dt": closed, "pnl_dec": pnl})
        return out

    def r_multiples(self, start: datetime) -> tuple[Any, ...]:
        """One TradeRMultiple per deal closed since ``start`` (partials are summed)."""
        from trader.execution.pnl import TradeRMultiple

        one_r = self.one_r_values()
        totals: dict[str, Decimal] = {}
        for item in self.results_since(start):
            deal = str(item.get("deal_id"))
            totals[deal] = totals.get(deal, Decimal(0)) + item["pnl_dec"]
        built = []
        for deal, total in totals.items():
            risk = one_r.get(deal, Decimal(0))
            built.append(TradeRMultiple(deal, total, risk, total / risk if risk else Decimal(0)))
        return tuple(built)

    def day_total(self, start: datetime) -> Decimal:
        return sum((item["pnl_dec"] for item in self.results_since(start)), Decimal(0))


def result_text(leg: ClosedLeg, reason: str, day_total: Decimal) -> str:
    when = leg.closed_at.astimezone(RIYADH).strftime("%d %b %H:%M") + " (Riyadh)"
    verdict = "WIN" if leg.pnl > 0 else "LOSS" if leg.pnl < 0 else "FLAT"
    lines = [
        f"RESULT {leg.epic}: {verdict} {money(leg.pnl, leg.currency)}",
        f"Time: {when}",
    ]
    if reason:
        lines.append(f"Closed by: {reason}")
    lines += ["", f"Today so far: {money(day_total, leg.currency)}"]
    return "\n".join(lines)


class ClosedTradeSync:
    """Poll Capital.com for new closed-trade lines; store them and tell the owner once."""

    def __init__(
        self,
        client: Any,
        ledger: TradeLedger,
        alert: Callable[[str], None],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._alert = alert
        self._now = now

    def poll(self) -> int:
        end = self._now() - timedelta(seconds=END_MARGIN_SECONDS)
        start = end - timedelta(hours=LOOKBACK_HOURS)
        stamp = self._client._timestamp
        payload = self._client._request_trading(
            "GET",
            "history/transactions",
            params={"from": stamp(start), "to": stamp(end), "type": "TRADE"},
            history=True,
        )
        rows = payload.get("transactions", []) if isinstance(payload, Mapping) else payload
        legs = parse_transactions(rows if isinstance(rows, list) else [])
        known = self._ledger.known_references()
        new = sorted(
            (leg for leg in legs if leg.reference not in known), key=lambda item: item.closed_at
        )
        if not new:
            return 0
        try:
            activities = self._client.get_activity(start, end)
        except Exception:  # noqa: BLE001 - the reason is a nice-to-have
            LOGGER.warning("closed-trade reasons unavailable", exc_info=True)
            activities = []
        day_start = (
            end.astimezone(RIYADH).replace(hour=0, minute=0, second=0, microsecond=0)
        ).astimezone(UTC)
        for leg in new:
            reason = closing_reason(leg, activities)
            self._ledger.record(leg, reason)
            if (end - leg.closed_at).total_seconds() <= FRESH_ALERT_SECONDS:
                self._alert(result_text(leg, reason, self._ledger.day_total(day_start)))
        return len(new)
