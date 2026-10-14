"""Hash-chain helpers for independently checking append-only audit records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import Engine, insert, select, text

from trader.core.db import tables


def audit_hash(previous_hash: str, payload: str) -> str:
    """Hash the exact UTF-8 payload preceded by its prior chain hash."""
    return hashlib.sha256((previous_hash + payload).encode("utf-8")).hexdigest()


def verify_audit_chain(entries: Iterable[Mapping[str, str]]) -> bool:
    """Verify ordered audit entries against their prior hash and payload."""
    previous_hash = ""
    for entry in entries:
        if entry.get("prev_hash") != previous_hash:
            return False
        expected_hash = audit_hash(previous_hash, entry.get("payload", ""))
        if entry.get("hash") != expected_hash:
            return False
        previous_hash = expected_hash
    return True


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Structured event stored in the append-only database audit log."""

    event_type: str
    payload: Mapping[str, Any]
    cycle_id: str | None = None


class AuditWriter(Protocol):
    """Narrow audit interface used by safety-sensitive components."""

    def append(self, event: AuditEvent) -> None:
        """Append one event or raise when durable audit storage is unavailable."""


class SqlAuditWriter:
    """Write events to ``audit_log`` and preserve its hash-chain convention."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, event: AuditEvent) -> None:
        """Append a canonical JSON payload in one database transaction.

        PostgreSQL's insert trigger serializes concurrent writers with an
        advisory transaction lock and recalculates the chain fields. The
        explicit calculation here also keeps SQLite-based unit tests useful.
        """
        payload = json.dumps(
            dict(event.payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        audit_table = tables["audit_log"]
        with self._engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                # Acquire the same transaction lock as the trigger before the
                # identity sequence is allocated by INSERT. This keeps sequence
                # order aligned with the serialized chain order.
                connection.execute(text("SELECT pg_advisory_xact_lock(94210857)"))
            previous_row = (
                connection.execute(
                    select(audit_table.c.sequence, audit_table.c.hash)
                    .order_by(audit_table.c.sequence.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            previous = previous_row["hash"] if previous_row is not None else ""
            values: dict[str, object] = {
                "id": str(uuid4()),
                "cycle_id": event.cycle_id,
                "event_type": event.event_type,
                "payload": payload,
                "prev_hash": previous,
                "hash": audit_hash(previous, payload),
            }
            if connection.dialect.name != "postgresql":
                # SQLite is used for fast unit tests and does not implement
                # this schema's non-primary-key Identity column.
                values["sequence"] = (
                    int(previous_row["sequence"]) + 1 if previous_row is not None else 1
                )
            connection.execute(insert(audit_table).values(**values))
