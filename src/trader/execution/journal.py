"""Durable execution intents, reserved before any broker submission."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from trader.core.db import tables
from trader.execution.brokers.base import Direction, OrderRequest


class JournalError(RuntimeError):
    """Persistence failed or a cycle was reused with different parameters."""


def order_payload(order: OrderRequest) -> dict[str, object]:
    return {
        "client_order_id": order.client_order_id,
        "epic": order.epic,
        "direction": order.direction.value,
        "size": str(order.size),
        "reference_price": str(order.reference_price),
        "stop_level": str(order.stop_level),
        "profit_level": str(order.profit_level) if order.profit_level is not None else None,
    }


def restored_order(payload: Mapping[str, Any]) -> OrderRequest:
    return OrderRequest(
        client_order_id=str(payload["client_order_id"]),
        epic=str(payload["epic"]),
        direction=Direction(payload["direction"]),
        size=Decimal(payload["size"]),
        reference_price=Decimal(payload["reference_price"]),
        stop_level=Decimal(payload["stop_level"]),
        profit_level=Decimal(payload["profit_level"]) if payload["profit_level"] else None,
    )


class ExecutionJournal:
    """Uses the existing trade_intents primary key as the cycle reservation.

    A committed reservation is permanent, including rejected and ambiguous
    submissions. Recovery never deletes it or sends it again. PostgreSQL's
    unique constraint arbitrates across processes, independently of Redis.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._table = tables["trade_intents"]

    def save_pause(self, reason: str) -> None:
        """Latch a pause across process restarts, independently of Redis."""
        table = tables["snapshots"]
        try:
            with self._engine.begin() as connection:
                exists = connection.execute(
                    select(table.c.id).where(table.c.id == "execution-pause")
                ).scalar_one_or_none()
                if exists is None:
                    connection.execute(
                        insert(table).values(id="execution-pause", payload={"reason": reason})
                    )
                else:
                    connection.execute(
                        update(table)
                        .where(table.c.id == "execution-pause")
                        .values(payload={"reason": reason})
                    )
        except Exception:
            raise JournalError("execution pause persistence failed") from None

    def save_kill(self, reason: str) -> None:
        """Durably latch kill across service restarts until explicit owner recovery."""
        table = tables["snapshots"]
        try:
            with self._engine.begin() as connection:
                exists = connection.execute(
                    select(table.c.id).where(table.c.id == "execution-kill")
                ).scalar_one_or_none()
                if exists is None:
                    connection.execute(
                        insert(table).values(id="execution-kill", payload={"reason": reason})
                    )
                else:
                    connection.execute(
                        update(table)
                        .where(table.c.id == "execution-kill")
                        .values(payload={"reason": reason})
                    )
        except Exception:
            raise JournalError("execution kill persistence failed") from None

    def is_killed(self) -> bool:
        table = tables["snapshots"]
        try:
            with self._engine.connect() as connection:
                return (
                    connection.execute(
                        select(table.c.id).where(table.c.id == "execution-kill")
                    ).scalar_one_or_none()
                    is not None
                )
        except Exception:
            raise JournalError("execution kill read failed") from None

    def clear_kill(self) -> None:
        from sqlalchemy import delete

        try:
            with self._engine.begin() as connection:
                table = tables["snapshots"]
                connection.execute(delete(table).where(table.c.id == "execution-kill"))
        except Exception:
            raise JournalError("execution kill reset failed") from None

    def is_paused(self) -> bool:
        table = tables["snapshots"]
        try:
            with self._engine.connect() as connection:
                return (
                    connection.execute(
                        select(table.c.id).where(table.c.id == "execution-pause")
                    ).scalar_one_or_none()
                    is not None
                )
        except Exception:
            raise JournalError("execution pause read failed") from None

    def clear_pause(self) -> None:
        from sqlalchemy import delete

        try:
            with self._engine.begin() as connection:
                table = tables["snapshots"]
                connection.execute(delete(table).where(table.c.id == "execution-pause"))
        except Exception:
            raise JournalError("execution pause reset failed") from None

    def reserve(self, cycle_id: str, order: OrderRequest) -> bool:
        if not cycle_id or len(cycle_id) > 36 or order.client_order_id != cycle_id:
            raise JournalError("client_order_id must equal the cycle_id (maximum 36 characters)")
        payload = {
            "execution_order": order_payload(order),
            "execution_state": "reserved",
            "reserved_at": datetime.now(UTC).isoformat(),
            "initial_stop": str(order.stop_level),
        }
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(self._table).values(id=cycle_id, cycle_id=cycle_id, payload=payload)
                )
            return True
        except IntegrityError:
            existing = self.get(cycle_id)
            if existing is None or existing.get("execution_order") != order_payload(order):
                raise JournalError(
                    "cycle_id was already reserved for different parameters"
                ) from None
            return False
        except Exception:
            raise JournalError("execution intent reservation failed") from None

    def get(self, cycle_id: str) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as connection:
                payload = connection.execute(
                    select(self._table.c.payload).where(self._table.c.id == cycle_id)
                ).scalar_one_or_none()
            if payload is None:
                return None
            if not isinstance(payload, dict) or "execution_order" not in payload:
                raise JournalError("stored execution intent is invalid")
            return cast(dict[str, Any], payload)
        except Exception:
            raise JournalError("execution intent read failed") from None

    def records(self) -> tuple[dict[str, Any], ...]:
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(select(self._table.c.payload)).scalars().all()
            return tuple(
                cast(dict[str, Any], row)
                for row in rows
                if isinstance(row, dict) and "execution_order" in row
            )
        except Exception:
            raise JournalError("execution recovery read failed") from None

    def mark(
        self,
        cycle_id: str,
        state: str,
        *,
        deal_id: str | None = None,
        resolution_reason: str | None = None,
    ) -> None:
        """Record outcome without changing order parameters or initial stop."""
        try:
            with self._engine.begin() as connection:
                row = connection.execute(
                    select(self._table.c.payload)
                    .where(self._table.c.id == cycle_id)
                    .with_for_update()
                ).scalar_one()
                payload = dict(row)
                if "execution_order" not in payload:
                    raise JournalError("execution reservation missing")
                payload["execution_state"] = state
                if deal_id is not None:
                    payload["deal_id"] = deal_id
                if resolution_reason is not None:
                    resolutions = list(payload.get("operator_resolutions", []))
                    resolutions.append(
                        {
                            "state": state,
                            "deal_id": deal_id,
                            "reason": resolution_reason,
                            "at": datetime.now(UTC).isoformat(),
                        }
                    )
                    payload["operator_resolutions"] = resolutions
                connection.execute(
                    update(self._table).where(self._table.c.id == cycle_id).values(payload=payload)
                )
        except Exception:
            raise JournalError("execution outcome write failed") from None
