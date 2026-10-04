"""Persistent UTC budget reservations and LLM usage settlement tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select

from trader.agents.llm import BudgetExceeded, SqlUsageLedger
from trader.core.db import metadata, tables


def test_usage_ledger_enforces_daily_and_monthly_caps() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    ledger = SqlUsageLedger(
        engine,
        daily_cap_usd=Decimal("4"),
        monthly_cap_usd=Decimal("80"),
        clock=lambda: now,
    )

    first = ledger.reserve(role="planner", model="m", maximum_cost=Decimal("2.5"))
    ledger.settle(
        first, input_tokens=20, output_tokens=10, cost_usd=Decimal("1.25"), latency_ms=40,
        outcome="success",
    )
    second = ledger.reserve(role="planner", model="m", maximum_cost=Decimal("2.75"))
    with pytest.raises(BudgetExceeded):
        ledger.reserve(role="planner", model="m", maximum_cost=Decimal("1.0"))

    with engine.connect() as connection:
        rows = connection.execute(
            select(
                tables["llm_usage"].c.reserved_cost_usd,
                tables["llm_usage"].c.outcome,
            ).order_by(tables["llm_usage"].c.created_at)
        ).all()
    assert len(rows) == 2
    assert rows[0] == (Decimal("1.25000000"), "success")
    assert rows[1] == (Decimal("2.75000000"), "reserved")
    assert second


def test_usage_ledger_preserves_full_reservation_after_uncertain_failure() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    ledger = SqlUsageLedger(
        engine,
        daily_cap_usd=Decimal("4"),
        monthly_cap_usd=Decimal("80"),
    )
    reservation = ledger.reserve(role="planner", model="m", maximum_cost=Decimal("0.25"))
    ledger.settle(
        reservation, input_tokens=0, output_tokens=0, cost_usd=Decimal(0), latency_ms=5,
        outcome="error",
    )

    with engine.connect() as connection:
        row = connection.execute(
            select(
                tables["llm_usage"].c.reserved_cost_usd,
                tables["llm_usage"].c.cost_usd,
                tables["llm_usage"].c.outcome,
            )
        ).one()
    assert row == (Decimal("0.25000000"), Decimal("0.25000000"), "error")

