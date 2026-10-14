"""Append-only experiment ledger used by the Strategy Lab.

The ledger deliberately stores a JSON payload in the existing append-only
``experiment_ledger`` table.  A small in-memory implementation keeps the Lab
usable in offline tests while the SQL implementation provides durable writes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

if TYPE_CHECKING:
    from sqlalchemy import Engine


class ExperimentLedgerProtocol(Protocol):
    """Structural protocol documented without importing typing.Protocol at runtime."""

    def append(self, record: ExperimentRecord) -> ExperimentRecord: ...

    def records(self) -> tuple[ExperimentRecord, ...]: ...

    def count(self) -> int: ...

    def count_for_strategy(self, strategy_hash: str) -> int: ...


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    """One immutable strategy variant attempt, including failed attempts."""

    strategy_hash: str
    parameters: Mapping[str, Any]
    data_start: datetime
    data_end: datetime
    metrics: Mapping[str, Any]
    proposer: str = "unknown"
    holdout_accessed: bool = False
    experiment_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value["data_start"] = self.data_start.isoformat()
        value["data_end"] = self.data_end.isoformat()
        value["created_at"] = self.created_at.isoformat()
        return value


class ExperimentLedger:
    """Thread-safe-by-convention append-only ledger for offline runs."""

    def __init__(self, records: Iterable[ExperimentRecord] = ()) -> None:
        self._records = list(records)

    def append(self, record: ExperimentRecord) -> ExperimentRecord:
        if any(item.experiment_id == record.experiment_id for item in self._records):
            raise ValueError("experiment_id already exists; ledger is append-only")
        self._records.append(record)
        return record

    def records(self) -> tuple[ExperimentRecord, ...]:
        return tuple(self._records)

    def count(self) -> int:
        return len(self._records)

    def count_for_strategy(self, strategy_hash: str) -> int:
        return sum(item.strategy_hash == strategy_hash for item in self._records)

    def export_parquet(self, path: str | Path) -> Path:
        """Export a snapshot without changing the source ledger."""
        destination = Path(path)
        try:
            import pyarrow as pa  # type: ignore[import-untyped]
            import pyarrow.parquet as pq  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - dependency is pinned in CI
            raise RuntimeError("pyarrow is required for Parquet ledger export") from exc
        rows = [json.dumps(record.payload(), sort_keys=True) for record in self._records]
        table = pa.table(
            {"experiment_id": [r.experiment_id for r in self._records], "payload": rows}
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, destination)
        return destination


class SqlExperimentLedger:
    """Durable append-only ledger backed by the shared SQLAlchemy metadata."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, record: ExperimentRecord) -> ExperimentRecord:
        from sqlalchemy import insert, select

        from trader.core.db import tables

        table = tables["experiment_ledger"]
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(table.c.id).where(table.c.id == record.experiment_id)
            ).first()
            if existing is not None:
                raise ValueError("experiment_id already exists; ledger is append-only")
            connection.execute(
                insert(table).values(id=record.experiment_id, payload=record.payload())
            )
        return record

    def records(self) -> tuple[ExperimentRecord, ...]:
        import json

        from sqlalchemy import select

        from trader.core.db import tables

        table = tables["experiment_ledger"]
        with self._engine.connect() as connection:
            rows = connection.execute(select(table.c.payload)).mappings().all()
        result: list[ExperimentRecord] = []
        for row in rows:
            value = row["payload"]
            payload = json.loads(value) if isinstance(value, str) else value
            result.append(
                ExperimentRecord(
                    strategy_hash=payload["strategy_hash"],
                    parameters=payload["parameters"],
                    data_start=datetime.fromisoformat(payload["data_start"]),
                    data_end=datetime.fromisoformat(payload["data_end"]),
                    metrics=payload["metrics"],
                    proposer=payload.get("proposer", "unknown"),
                    holdout_accessed=payload.get("holdout_accessed", False),
                    experiment_id=payload["experiment_id"],
                    created_at=datetime.fromisoformat(payload["created_at"]),
                )
            )
        return tuple(result)

    def count(self) -> int:
        from sqlalchemy import func, select

        from trader.core.db import tables

        with self._engine.connect() as connection:
            statement = select(func.count()).select_from(tables["experiment_ledger"])
            return int(connection.execute(statement).scalar_one())

    def count_for_strategy(self, strategy_hash: str) -> int:
        return sum(item.strategy_hash == strategy_hash for item in self.records())


__all__ = [
    "ExperimentLedger",
    "ExperimentLedgerProtocol",
    "ExperimentRecord",
    "SqlExperimentLedger",
]
