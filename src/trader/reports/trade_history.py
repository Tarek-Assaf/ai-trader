"""Recent Tai trades for the owner, built from the bot's own audit log."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.engine import Engine

from trader.core.db import tables

OWNER_ZONE = ZoneInfo("Asia/Riyadh")
EVENTS = (
    "entry",
    "stop_to_entry",
    "scale_out",
    "exit",
    "reconcile_closed",
    "closed_for_new_signal",
    "trade_result",
)


def _stamp(value: datetime) -> str:
    return value.astimezone(OWNER_ZONE).strftime("%d %b %H:%M")


def _num(value: object) -> str:
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, ValueError):
        return str(value)


def summarize(rows: list[tuple[datetime, str, Mapping[str, Any]]], limit: int = 8) -> str:
    """Group audit rows by deal and describe the most recent ``limit`` trades."""
    trades: dict[str, dict[str, Any]] = {}
    for at, event, payload in rows:
        deal = str(payload.get("deal_id", ""))
        if not deal:
            continue
        trade = trades.setdefault(deal, {"hits": [], "banked_r": Decimal(0), "stop_moved": False})
        if event == "entry":
            trade.update(opened=at, details=payload)
        elif event == "stop_to_entry":
            trade["stop_moved"] = True
        elif event == "scale_out":
            index = int(str(payload.get("target", 0)) or 0)
            trade["hits"].append((index, _num(payload.get("amount", "?"))))
            try:
                trade["banked_r"] += Decimal(str(payload.get("fraction", "0"))) * index
            except InvalidOperation:
                pass
        elif event == "trade_result":
            try:
                trade["result"] = trade.get("result", Decimal(0)) + Decimal(str(payload["pnl"]))
                trade["currency"] = str(payload.get("currency", "USD"))
                trade["result_reason"] = str(payload.get("reason", "")) or trade.get(
                    "result_reason", ""
                )
            except (KeyError, InvalidOperation):
                pass
        elif event in {"exit", "reconcile_closed", "closed_for_new_signal"}:
            trade["closed"] = at
            trade["how"] = {
                "exit": "TP3 reached",
                "reconcile_closed": "closed by the broker (stop or TP3)",
                "closed_for_new_signal": "closed for a new TradingView signal",
            }[event]
    opened = [(deal, t) for deal, t in trades.items() if "opened" in t]
    if not opened:
        return "No trades recorded yet."
    opened.sort(key=lambda item: item[1]["opened"], reverse=True)
    lines = ["Recent trades (newest first):"]
    for number, (_, trade) in enumerate(opened[:limit], start=1):
        details = trade["details"]
        direction = details.get("direction", "")
        size = _num(details["size"]) if "size" in details else ""
        head = " ".join(
            part for part in (str(details.get("epic", "")), str(direction), size) if part
        )
        status = (
            f"closed {_stamp(trade['closed'])}, {trade['how']}" if "closed" in trade else "OPEN"
        )
        lines.append(f"{number}. {head} - opened {_stamp(trade['opened'])}, {status}")
        if "entry_price" in details:
            lines.append(
                f"   entry {_num(details['entry_price'])}  SL {_num(details.get('stop', '?'))}  "
                f"TP {_num(details.get('tp1', '?'))} / {_num(details.get('tp2', '?'))} / "
                f"{_num(details.get('tp3', '?'))}"
            )
        parts = [f"TP{index} hit ({amount})" for index, amount in sorted(trade["hits"])]
        if trade["stop_moved"]:
            parts.append("stop moved to entry")
        if parts:
            lines.append("   " + ", ".join(parts))
        if "result" in trade:
            sign = "-" if trade["result"] < 0 else "+"
            symbol = "$" if trade.get("currency") == "USD" else f"{trade.get('currency', '')} "
            reason = f" ({trade['result_reason']})" if trade.get("result_reason") else ""
            lines.append(f"   Result: {sign}{symbol}{abs(trade['result']):.2f}{reason}")
        if trade["hits"]:
            banked = f"{trade['banked_r']:+.2f}R banked from partials"
            one_r = details.get("one_r_value")
            if one_r not in (None, ""):
                try:
                    banked += f" (about {trade['banked_r'] * Decimal(str(one_r)):+.2f} USD)"
                except InvalidOperation:
                    pass
            lines.append("   " + banked)
        elif "closed" in trade and "result" not in trade:
            lines.append("   no target hit; result is in the Capital.com history")
    lines.append("Results come from Capital.com's history; day total: /pnl.")
    return "\n".join(lines)


def trade_history_text(engine: Engine, limit: int = 8) -> str:
    """Read the Tai trade events from ``audit_log`` and summarise them."""
    table = tables["audit_log"]
    with engine.connect() as connection:
        records = connection.execute(
            select(table.c.created_at, table.c.event_type, table.c.payload)
            .where(table.c.event_type.in_(EVENTS))
            .order_by(table.c.sequence.desc())
            .limit(400)
        ).all()
    rows: list[tuple[datetime, str, Mapping[str, Any]]] = []
    for at, event, raw in reversed(records):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            rows.append((at, str(event), payload))
    return summarize(rows, limit)
