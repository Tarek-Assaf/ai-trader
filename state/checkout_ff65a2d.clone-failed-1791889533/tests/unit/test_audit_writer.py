from __future__ import annotations

import json

from sqlalchemy import create_engine, select

from trader.core.audit import AuditEvent, SqlAuditWriter, verify_audit_chain
from trader.core.db import metadata, tables


def test_sql_audit_writer_appends_canonical_hash_chained_rows() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    writer = SqlAuditWriter(engine)

    writer.append(
        AuditEvent(
            event_type="risk.decision",
            cycle_id="cycle-1",
            payload={"approved": False, "reasons": ["has_stop:stop_loss_required"]},
        )
    )
    writer.append(
        AuditEvent(
            event_type="risk.decision",
            cycle_id="cycle-2",
            payload={"approved": True, "approved_size": "1"},
        )
    )

    with engine.connect() as connection:
        rows = (
            connection.execute(
                select(
                    tables["audit_log"].c.event_type,
                    tables["audit_log"].c.cycle_id,
                    tables["audit_log"].c.payload,
                    tables["audit_log"].c.prev_hash,
                    tables["audit_log"].c.hash,
                ).order_by(tables["audit_log"].c.sequence)
            )
            .mappings()
            .all()
        )

    entries = [
        {"prev_hash": row["prev_hash"], "payload": row["payload"], "hash": row["hash"]}
        for row in rows
    ]
    assert len(rows) == 2
    assert all(row["event_type"] == "risk.decision" for row in rows)
    assert [row["cycle_id"] for row in rows] == ["cycle-1", "cycle-2"]
    assert json.loads(rows[0]["payload"]) == {
        "approved": False,
        "reasons": ["has_stop:stop_loss_required"],
    }
    assert verify_audit_chain(entries)
