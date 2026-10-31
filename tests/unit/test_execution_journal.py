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


def _two_account_journals(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'execution.db'}")
    tables["trade_intents"].create(engine)
    tables["snapshots"].create(engine)
    return engine, ExecutionJournal(engine), ExecutionJournal(engine, "acct-02")


def test_account_ids_must_look_like_acct_nn():
    engine = create_engine("sqlite://")
    for bad in ("", "02", "acct-2", "acct-02; drop", "ACCT-02"):
        with pytest.raises(JournalError):
            ExecutionJournal(engine, bad)


def test_pause_and_kill_latches_are_separate_per_account(tmp_path):
    _engine, first, second = _two_account_journals(tmp_path)
    second.save_pause("order_outcome_ambiguous")
    assert second.is_paused() and second.pause_reason() == "order_outcome_ambiguous"
    assert not first.is_paused() and first.pause_reason() is None

    first.save_kill("external_kill")
    assert first.is_killed() and not second.is_killed()

    second.clear_pause()
    assert not second.is_paused()
    assert first.is_killed()
    first.clear_kill()
    assert not first.is_killed()


def test_account_01_keeps_the_original_snapshot_ids(tmp_path):
    from trader.execution.journal import kill_snapshot_id, pause_snapshot_id

    engine, first, second = _two_account_journals(tmp_path)
    assert pause_snapshot_id("acct-01") == "execution-pause"
    assert kill_snapshot_id("acct-01") == "execution-kill"
    assert pause_snapshot_id("acct-02") == "execution-pause:acct-02"
    first.save_pause("x")
    second.save_pause("y")
    rows = {
        row[0]
        for row in engine.connect().execute(
            tables["snapshots"].select().with_only_columns(tables["snapshots"].c.id)
        )
    }
    assert rows == {"execution-pause", "execution-pause:acct-02"}


def test_records_only_return_the_accounts_own_orders(tmp_path):
    _engine, first, second = _two_account_journals(tmp_path)
    one = OrderRequest("c-1", "US100", Direction.BUY, Decimal("1"), Decimal("100"), Decimal("90"))
    two = OrderRequest(
        "a02-c-1", "US100", Direction.BUY, Decimal("2"), Decimal("100"), Decimal("90")
    )
    assert first.reserve("c-1", one)
    assert second.reserve("a02-c-1", two)

    assert [r["execution_order"]["client_order_id"] for r in first.records()] == ["c-1"]
    assert [r["execution_order"]["client_order_id"] for r in second.records()] == ["a02-c-1"]
    assert first.get("c-1")["account"] == "acct-01"
    assert second.get("a02-c-1")["account"] == "acct-02"


def test_rows_written_before_accounts_existed_belong_to_account_01(tmp_path):
    engine, first, second = _two_account_journals(tmp_path)
    legacy = {
        "execution_order": {
            "client_order_id": "old-1",
            "epic": "US100",
            "direction": "BUY",
            "size": "1",
            "reference_price": "100",
            "stop_level": "90",
            "profit_level": None,
        },
        "execution_state": "submitted",
        "deal_id": "deal-9",
    }
    with engine.begin() as connection:
        connection.execute(
            tables["trade_intents"].insert().values(id="old-1", cycle_id="old-1", payload=legacy)
        )
    assert [r["execution_order"]["client_order_id"] for r in first.records()] == ["old-1"]
    assert second.records() == ()
