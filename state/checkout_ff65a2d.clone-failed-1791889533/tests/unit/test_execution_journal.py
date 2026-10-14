"""Durable reservation survives restarts and forbids parameter substitution."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from trader.core.db import tables
from trader.execution.brokers.base import Direction, OrderRequest
from trader.execution.journal import ExecutionJournal, JournalError


def order(size: str = "1") -> OrderRequest:
    return OrderRequest(
        "cycle-1", "US100", Direction.BUY, Decimal(size), Decimal("100"), Decimal("90")
    )


def test_reservation_survives_restart_and_retains_initial_stop(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'execution.db'}")
    tables["trade_intents"].create(engine)
    journal = ExecutionJournal(engine)
    assert journal.reserve("cycle-1", order())
    journal.mark("cycle-1", "submitted", deal_id="deal-1")
    engine.dispose()
    recovered = ExecutionJournal(create_engine(f"sqlite:///{tmp_path / 'execution.db'}"))
    assert not recovered.reserve("cycle-1", order())
    assert recovered.get("cycle-1")["initial_stop"] == "90"
    assert recovered.get("cycle-1")["deal_id"] == "deal-1"
    with pytest.raises(JournalError):
        recovered.reserve("cycle-1", order("2"))


def test_client_order_id_must_equal_cycle():
    journal = ExecutionJournal(create_engine("sqlite://"))
    with pytest.raises(JournalError):
        journal.reserve("other-cycle", order())


def test_database_failure_does_not_return_a_successful_reservation():
    journal = ExecutionJournal(create_engine("sqlite://"))
    with pytest.raises(JournalError):
        journal.reserve("cycle-1", order())
