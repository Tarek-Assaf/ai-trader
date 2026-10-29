"""One line telling the owner whether the bot will take the next signal."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from trader.core.db import tables


def entry_state_lines(engine: Engine | None, mode: Callable[[], Any] | None) -> list[str]:
    """Control mode plus the latched execution pause (and its reason), if any."""
    lines: list[str] = []
    if callable(mode):
        try:
            value = mode()
            lines.append(f"Control mode: {getattr(value, 'value', value)}")
        except Exception:  # noqa: BLE001 - a status line must never fail the command
            lines.append("Control mode: unknown")
    if engine is None:
        lines.append("Entries: unknown (database unavailable)")
        return lines
    table = tables["snapshots"]
    try:
        with engine.connect() as connection:
            payload = connection.execute(
                select(table.c.payload).where(table.c.id == "execution-pause")
            ).first()
    except Exception:  # noqa: BLE001
        lines.append("Entries: unknown (could not read the pause state)")
        return lines
    if payload is None:
        lines.append("Entries: active (no pause latched)")
    else:
        body = payload[0]
        reason = body.get("reason") if isinstance(body, dict) else None
        lines.append(f"Entries: PAUSED ({reason or 'no reason recorded'}). Send /resume.")
    return lines
