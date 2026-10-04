"""Database migration and append-only enforcement checks for PostgreSQL CI."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from trader.core.audit import AuditEvent, SqlAuditWriter
from trader.core.db import tables

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not configured")


def test_migrations_create_tables_and_audit_log_is_append_only() -> None:
    """The empty-database migration creates required tables and SQL guards."""
    engine = create_engine(DATABASE_URL or "")
    expected = {
        "cycles",
        "decisions",
        "trade_intents",
        "orders",
        "fills",
        "positions",
        "snapshots",
        "research_items",
        "lessons",
        "audit_log",
        "worker_heartbeats",
        "autoscale_events",
        "settings_versions",
        "strategy_stages",
        "experiment_ledger",
        "data_lineage",
    }
    assert expected.issubset(set(inspect(engine).get_table_names()))
    record_id = str(uuid4())
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO audit_log (id, event_type, payload) VALUES (%s, 'test', %s)",
            (record_id, '{"check":true}'),
        )
        assert connection.execute(text("SELECT verify_audit_log()")).scalar_one() is True
    with pytest.raises(DBAPIError, match="audit_log is append-only"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE audit_log SET payload='{}' WHERE id=:id"), {"id": record_id}
            )
    with pytest.raises(DBAPIError, match="audit_log is append-only"):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM audit_log WHERE id=:id"), {"id": record_id})
    with pytest.raises(DBAPIError, match="audit_log is append-only"):
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE audit_log"))
    engine.dispose()

def test_sql_audit_writer_serializes_and_chains_risk_decisions() -> None:
    """The production writer uses the DB lock and trigger for a valid chain."""
    engine = create_engine(DATABASE_URL or "")
    cycle_id = str(uuid4())
    writer = SqlAuditWriter(engine)

    writer.append(
        AuditEvent(
            event_type="risk.decision",
            cycle_id=cycle_id,
            payload={"approved": False, "reasons": ["has_stop:stop_loss_required"]},
        )
    )
    writer.append(
        AuditEvent(
            event_type="risk.decision",
            cycle_id=cycle_id,
            payload={"approved": True, "approved_size": "1"},
        )
    )

    with engine.connect() as connection:
        rows = (
            connection.execute(
                text("SELECT event_type FROM audit_log WHERE cycle_id=:cycle_id ORDER BY sequence"),
                {"cycle_id": cycle_id},
            )
            .scalars()
            .all()
        )
        assert rows == ["risk.decision", "risk.decision"]
        assert connection.execute(text("SELECT verify_audit_log()")).scalar_one() is True
        stored = (
            connection.execute(
                tables["audit_log"].select().where(tables["audit_log"].c.cycle_id == cycle_id)
            )
            .mappings()
            .all()
        )
        assert len(stored) == 2
        assert stored[0]["prev_hash"] == "" or len(stored[0]["prev_hash"]) == 64
        assert len(stored[0]["hash"]) == 64
        assert stored[1]["prev_hash"] == stored[0]["hash"]
    engine.dispose()
