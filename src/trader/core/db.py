"""SQLAlchemy metadata shared by the runtime and Alembic migrations."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    JSON,
    MetaData,
    Numeric,
    String,
    Table,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
TABLE_NAMES = (
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
)
_JSON = JSON().with_variant(cast(Any, JSONB)(), "postgresql")


def _record_table(name: str) -> Table:
    """Create a common record table with JSON payload and an optional cycle link."""
    columns = [
        Column("id", String(36), primary_key=True),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ]
    if name != "cycles":
        columns.append(Column("cycle_id", String(36), nullable=True, index=True))
    if name == "audit_log":
        columns.extend(
            [
                Column("sequence", BigInteger, Identity(), nullable=False, unique=True),
                Column("event_type", String(120), nullable=False),
                Column("payload", String, nullable=False),
                Column("prev_hash", String(64), nullable=False, server_default=""),
                Column("hash", String(64), nullable=False, server_default=""),
            ]
        )
    else:
        columns.append(Column("payload", _JSON, nullable=True))
    return Table(name, metadata, *cast(list[Any], columns))


tables: dict[str, Table] = {name: _record_table(name) for name in TABLE_NAMES}

tables["llm_usage"] = Table(
    "llm_usage",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("role", String(120), nullable=False),
    Column("model", String(240), nullable=False),
    Column("input_tokens", Integer, nullable=False, server_default="0"),
    Column("output_tokens", Integer, nullable=False, server_default="0"),
    Column("cost_usd", Numeric(18, 8), nullable=False, server_default="0"),
    Column("reserved_cost_usd", Numeric(18, 8), nullable=False),
    Column("latency_ms", Integer, nullable=False, server_default="0"),
    Column("outcome", String(32), nullable=False),
)
Index("ix_llm_usage_created_at", tables["llm_usage"].c.created_at)
