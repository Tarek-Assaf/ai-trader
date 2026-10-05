"""Account selection and persisted initial-risk reconciliation."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from trader.core.db import tables
from trader.execution.brokers.base import Direction, OrderRequest
from trader.execution.journal import ExecutionJournal
from trader.execution.pnl import BrokerAccountFigures, PnlTrade
from trader.execution.reconciliation import (
    CapitalAccountFieldMap,
    normalize_capital_account,
    reconcile_pnl,
)


def test_account_mapping_requires_explicit_id_fields_and_valuation():
    raw = {
        "accounts": [
            {
                "accountId": "active",
                "currency": "USD",
                "balance": {
                    "deposit": "1000",
                    "balance": "1010",
                    "profitLoss": "10",
                    "available": "910",
                },
            }
        ]
    }
    result = normalize_capital_account(
        raw,
        account_id="active",
        fields=CapitalAccountFieldMap("deposit", "balance"),
        margin_used=Decimal("100"),
        notional_exposure=Decimal("2000"),
    )
    assert result == BrokerAccountFigures(
        Decimal("1000"), Decimal("1010"), "USD", Decimal("100"), Decimal("2000")
    )
    with pytest.raises(ValueError):
        normalize_capital_account(
            raw,
            account_id="missing",
            fields=CapitalAccountFieldMap("deposit", "balance"),
            margin_used=Decimal("0"),
            notional_exposure=Decimal("0"),
        )


def test_initial_stop_is_used_for_r_multiple_and_mismatch_raises():
    engine = create_engine("sqlite://")
    tables["trade_intents"].create(engine)
    journal = ExecutionJournal(engine)
    journal.reserve(
        "cycle",
        OrderRequest("cycle", "US100", Direction.BUY, Decimal("1"), Decimal("100"), Decimal("90")),
    )
    journal.mark("cycle", "submitted", deal_id="deal")
    trade = PnlTrade(
        "deal",
        "US100",
        Direction.BUY,
        Decimal("1"),
        Decimal("100"),
        Decimal("95"),
        Decimal("1"),
        Decimal("0"),
        Decimal("0"),
        exit_price=Decimal("110"),
    )
    broker = BrokerAccountFigures(
        Decimal("1010"), Decimal("1010"), "USD", Decimal("0"), Decimal("0")
    )
    report = reconcile_pnl(
        [trade], journal, starting_balance=Decimal("1000"), currency="USD", broker=broker
    )
    assert report.r_multiples[0].initial_risk == Decimal("10")
    assert report.r_multiples[0].r_multiple == Decimal("1")
    wrong = BrokerAccountFigures(
        Decimal("1000"), Decimal("1000"), "USD", Decimal("0"), Decimal("0")
    )
    with pytest.raises(ValueError, match="exceeds tolerance"):
        reconcile_pnl(
            [trade], journal, starting_balance=Decimal("1000"), currency="USD", broker=wrong
        )
