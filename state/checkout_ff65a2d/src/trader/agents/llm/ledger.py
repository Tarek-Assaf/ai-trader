"""Durable, fail-closed spend reservations shared by all model callers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Engine, func, insert, select, update

from trader.agents.llm.types import BudgetExceeded
from trader.core.db import metadata, tables


class SqlUsageLedger:
    """Store each model call and reserve its maximum possible cost before sending."""

    def __init__(
        self,
        engine: Engine,
        *,
        daily_cap_usd: Decimal,
        monthly_cap_usd: Decimal,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            not daily_cap_usd.is_finite()
            or not monthly_cap_usd.is_finite()
            or daily_cap_usd <= 0
            or monthly_cap_usd <= 0
            or daily_cap_usd > monthly_cap_usd
        ):
            raise ValueError("LLM spend caps must be positive and daily must not exceed monthly")
        self.engine = engine
        self.daily_cap_usd = daily_cap_usd
        self.monthly_cap_usd = monthly_cap_usd
        self._clock = clock
        metadata.create_all(engine, tables=[tables["llm_usage"]])

    def reserve(self, *, role: str, model: str, maximum_cost: Decimal) -> str:
        """Atomically reserve worst-case cost or reject before any outbound request."""
        maximum = Decimal(str(maximum_cost))
        if not role or not model or not maximum.is_finite() or maximum <= 0:
            raise ValueError("role, model and positive finite reservation are required")
        current_time = self._clock()
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("LLM usage clock must return timezone-aware time")
        now = current_time.astimezone(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        usage = tables["llm_usage"]
        row_id = str(uuid4())
        with self.engine.connect() as connection:
            try:
                if connection.dialect.name == "sqlite":
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                elif connection.dialect.name == "postgresql":
                    connection.begin()
                    connection.exec_driver_sql("SELECT pg_advisory_xact_lock(94210858)")
                else:
                    raise RuntimeError("LLM budget ledger requires PostgreSQL or SQLite")
                day_total = connection.execute(
                    select(func.coalesce(func.sum(usage.c.reserved_cost_usd), 0)).where(
                        usage.c.created_at >= day_start
                    )
                ).scalar_one()
                month_total = connection.execute(
                    select(func.coalesce(func.sum(usage.c.reserved_cost_usd), 0)).where(
                        usage.c.created_at >= month_start
                    )
                ).scalar_one()
                if (
                    Decimal(str(day_total)) + maximum > self.daily_cap_usd
                    or Decimal(str(month_total)) + maximum > self.monthly_cap_usd
                ):
                    connection.rollback()
                    raise BudgetExceeded("configured LLM daily or monthly spend cap reached")
                connection.execute(
                    insert(usage).values(
                        id=row_id,
                        created_at=now,
                        role=role,
                        model=model,
                        reserved_cost_usd=maximum,
                        outcome="reserved",
                    )
                )
                connection.commit()
            except Exception:
                if connection.in_transaction():
                    connection.rollback()
                raise
        return row_id

    def settle(
        self,
        reservation_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal,
        latency_ms: int,
        outcome: str,
    ) -> None:
        """Record validated usage; preserve the full reservation on uncertain failures."""
        cost = Decimal(str(cost_usd))
        if (
            input_tokens < 0
            or output_tokens < 0
            or latency_ms < 0
            or not cost.is_finite()
            or cost < 0
            or outcome not in {"success", "invalid_output", "error"}
        ):
            raise ValueError("invalid LLM usage record")
        usage = tables["llm_usage"]
        reserved = cost if outcome in {"success", "invalid_output"} else None
        values = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": usage.c.reserved_cost_usd if outcome == "error" else cost,
            "latency_ms": latency_ms,
            "outcome": outcome,
        }
        if reserved is not None:
            values["reserved_cost_usd"] = reserved
        with self.engine.begin() as connection:
            result = connection.execute(
                update(usage).where(usage.c.id == reservation_id).values(**values)
            )
            if result.rowcount != 1:
                raise RuntimeError("LLM usage reservation was not found")

    def summary(self, *, now: datetime | None = None) -> Mapping[str, Decimal | int]:
        """Return current token and cost totals for read-only reports.

        The query exposes aggregates only; prompts, model credentials and
        reservation identifiers never enter the report payload.
        """
        current_time = self._clock() if now is None else now
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("LLM usage clock must return timezone-aware time")
        current_time = current_time.astimezone(UTC)
        day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        usage = tables["llm_usage"]
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    func.coalesce(func.sum(usage.c.input_tokens), 0),
                    func.coalesce(func.sum(usage.c.output_tokens), 0),
                    func.coalesce(func.sum(usage.c.cost_usd), 0),
                    func.coalesce(
                        func.sum(usage.c.cost_usd).filter(usage.c.created_at >= day_start), 0
                    ),
                    func.coalesce(
                        func.sum(usage.c.cost_usd).filter(usage.c.created_at >= month_start), 0
                    ),
                )
            ).one()
        return {
            "input_tokens": int(row[0]),
            "output_tokens": int(row[1]),
            "cost_usd": Decimal(str(row[2])),
            "daily_cost_usd": Decimal(str(row[3])),
            "monthly_cost_usd": Decimal(str(row[4])),
        }
