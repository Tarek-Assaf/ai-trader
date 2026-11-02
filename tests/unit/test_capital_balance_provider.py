"""The balance read before each entry works for a login that holds more than one account."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trader.experiments.tai_demo import TaiDemoRefused
from trader.experiments.tai_wiring import CapitalBalanceProvider


class _Client:
    def __init__(self, accounts: list[dict]) -> None:
        self._accounts = accounts

    def account_info(self) -> dict:
        return {"accounts": self._accounts}


def _account(account_id: str, balance: float, preferred: bool | None = None) -> dict:
    row: dict = {"accountId": account_id, "balance": {"balance": balance}}
    if preferred is not None:
        row["preferred"] = preferred
    return row


def test_a_single_account_is_used() -> None:
    assert CapitalBalanceProvider(_Client([_account("A", 20987.63)]))() == Decimal("20987.63")


def test_with_several_accounts_the_preferred_one_is_used() -> None:
    client = _Client(
        [_account("A", 5, False), _account("B", 1000.0, True), _account("C", 7, False)]
    )
    assert CapitalBalanceProvider(client)() == Decimal("1000.0")


def test_several_accounts_and_no_clear_preferred_one_is_still_refused() -> None:
    for rows in (
        [_account("A", 5), _account("B", 6)],
        [_account("A", 5, True), _account("B", 6, True)],
        [_account("A", 5, False), _account("B", 6, False)],
    ):
        with pytest.raises(TaiDemoRefused, match="ambiguous"):
            CapitalBalanceProvider(_Client(rows))()


def test_an_explicit_account_id_still_wins_over_preferred() -> None:
    client = _Client([_account("A", 5, True), _account("B", 99, False)])
    assert CapitalBalanceProvider(client, account_id="B")() == Decimal("99")
