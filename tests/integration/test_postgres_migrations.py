"""Database migration and append-only enforcement checks for PostgreSQL CI."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

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
        connection.execute(
            text("INSERT INTO audit_log (id, event_type, payload) VALUES (:id, 'test', :payload)"),
            {"id": record_id, "payload": '{"check":true}'},
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

