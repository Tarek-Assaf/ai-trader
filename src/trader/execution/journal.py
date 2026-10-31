"""Durable execution intents, reserved before any broker submission."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from trader.core.db import tables
from trader.execution.brokers.base import Direction, OrderRequest

DEFAULT_ACCOUNT_ID = "acct-01"
_ACCOUNT_ID = re.compile(r"^acct-\d{2,3}$")


class JournalError(RuntimeError):
    """Persistence failed or a cycle was reused with different parameters."""


def pause_snapshot_id(account_id: str = DEFAULT_ACCOUNT_ID) -> str:
    """Snapshot id of an account's pause latch; account 01 keeps the original id."""
    return (
        "execution-pause" if account_id == DEFAULT_ACCOUNT_ID else f"execution-pause:{account_id}"
    )


def kill_snapshot_id(account_id: str = DEFAULT_ACCOUNT_ID) -> str:
    """Snapshot id of an account's kill latch; account 01 keeps the original id."""
    if account_id == DEFAULT_ACCOUNT_ID:
        return "execution-kill"
    return f"execution-kill:{account_id}"


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

    def __init__(self, engine: Engine, account_id: str = DEFAULT_ACCOUNT_ID) -> None:
        if not _ACCOUNT_ID.match(account_id):
            raise JournalError("account id must look like acct-01")
        self._engine = engine
        self._table = tables["trade_intents"]
        self._account_id = account_id
        self._pause_id = pause_snapshot_id(account_id)
        self._kill_id = kill_snapshot_id(account_id)

    @property
    def account_id(self) -> str:
        return self._account_id

    def save_pause(self, reason: str) -> None:
        """Latch a pause across process restarts, independently of Redis."""
        table = tables["snapshots"]
        try:
            with self._engine.begin() as connection:
                exists = connection.execute(
                    select(table.c.id).where(table.c.id == self._pause_id)
                ).scalar_one_or_none()
                if exists is None:
                    connection.execute(
                        insert(table).values(id=self._pause_id, payload={"reason": reason})
                    )
                else:
                    connection.execute(
                        update(table)
                        .where(table.c.id == self._pause_id)
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
                    select(table.c.id).where(table.c.id == self._kill_id)
                ).scalar_one_or_none()
                if exists is None:
                    connection.execute(
                        insert(table).values(id=self._kill_id, payload={"reason": reason})
                    )
                else:
                    connection.execute(
                        update(table)
                        .where(table.c.id == self._kill_id)
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
                        select(table.c.id).where(table.c.id == self._kill_id)
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
                connection.execute(delete(table).where(table.c.id == self._kill_id))
        except Exception:
            raise JournalError("execution kill reset failed") from None

    def is_paused(self) -> bool:
        table = tables["snapshots"]
        try:
            with self._engine.connect() as connection:
                return (
                    connection.execute(
                        select(table.c.id).where(table.c.id == self._pause_id)
                    ).scalar_one_or_none()
                    is not None
                )
        except Exception:
            raise JournalError("execution pause read failed") from None

    def pause_reason(self) -> str | None:
        """Reason stored with the pause latch, or None when no pause is latched."""
        table = tables["snapshots"]
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    select(table.c.payload).where(table.c.id == self._pause_id)
                ).first()
        except Exception:
            raise JournalError("execution pause read failed") from None
        if row is None:
            return None
        body = row[0]
        reason = body.get("reason") if isinstance(body, Mapping) else None
        return str(reason) if reason else "unknown"

    def clear_pause(self) -> None:
        from sqlalchemy import delete

        try:
            with self._engine.begin() as connection:
                table = tables["snapshots"]
                connection.execute(delete(table).where(table.c.id == self._pause_id))
        except Exception:
            raise JournalError("execution pause reset failed") from None

    def save_snapshot(self, snapshot_id: str, payload: Mapping[str, Any]) -> None:
        """Persist a small runtime snapshot used across process restarts."""
        if not snapshot_id.strip() or not isinstance(payload, Mapping):
            raise JournalError("snapshot id and payload are required")
        table = tables["snapshots"]
        try:
            with self._engine.begin() as connection:
                exists = connection.execute(
                    select(table.c.id).where(table.c.id == snapshot_id)
                ).scalar_one_or_none()
                values = {"id": snapshot_id, "payload": dict(payload)}
                if exists is None:
                    connection.execute(insert(table).values(**values))
                else:
                    connection.execute(
                        update(table).where(table.c.id == snapshot_id).values(payload=dict(payload))
                    )
        except Exception:
            raise JournalError("runtime snapshot persistence failed") from None

    def get_snapshot(self, snapshot_id: str) -> Mapping[str, Any] | None:
        """Read a runtime snapshot without exposing database exceptions."""
        if not snapshot_id.strip():
            raise JournalError("snapshot id is required")
        try:
            with self._engine.connect() as connection:
                payload = connection.execute(
                    select(tables["snapshots"].c.payload).where(
                        tables["snapshots"].c.id == snapshot_id
                    )
                ).scalar_one_or_none()
            return payload if isinstance(payload, Mapping) else None
        except Exception:
            raise JournalError("runtime snapshot read failed") from None

    def reserve(self, cycle_id: str, order: OrderRequest) -> bool:
        if not cycle_id or len(cycle_id) > 36 or order.client_order_id != cycle_id:
            raise JournalError("client_order_id must equal the cycle_id (maximum 36 characters)")
        payload = {
            "execution_order": order_payload(order),
            "execution_state": "reserved",
            "reserved_at": datetime.now(UTC).isoformat(),
            "initial_stop": str(order.stop_level),
            "account": self._account_id,
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
                if isinstance(row, dict)
                and "execution_order" in row
                and row.get("account", DEFAULT_ACCOUNT_ID) == self._account_id
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
