"""The saved start balance: recorded once from the account, never silently replaced."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from trader.experiments.start_balance import StartBalanceStore, StartBalanceUnreadable


def test_nothing_is_saved_until_a_balance_is_recorded(tmp_path: Path) -> None:
    store = StartBalanceStore(tmp_path / "state" / "start.json")
    assert store.get() is None


def test_the_first_balance_is_saved_and_reported_as_new(tmp_path: Path) -> None:
    store = StartBalanceStore(tmp_path / "state" / "start.json")
    assert store.record_if_missing(Decimal("20987.63")) == (Decimal("20987.63"), True)
    assert store.get() == Decimal("20987.63")


def test_a_later_balance_never_replaces_the_saved_one(tmp_path: Path) -> None:
    store = StartBalanceStore(tmp_path / "start.json")
    store.record_if_missing(Decimal("20987.63"))
    assert store.record_if_missing(Decimal("31000")) == (Decimal("20987.63"), False)
    assert store.get() == Decimal("20987.63")


def test_the_saved_balance_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "start.json"
    StartBalanceStore(path).record_if_missing(Decimal("20987.63"))
    reopened = StartBalanceStore(path)
    assert reopened.get() == Decimal("20987.63")
    assert reopened.record_if_missing(Decimal("5")) == (Decimal("20987.63"), False)


def test_each_account_workspace_keeps_its_own_balance(tmp_path: Path) -> None:
    first = StartBalanceStore(tmp_path / "a" / "start.json")
    second = StartBalanceStore(tmp_path / "b" / "start.json")
    first.record_if_missing(Decimal("20987.63"))
    second.record_if_missing(Decimal("5000"))
    assert first.get() == Decimal("20987.63")
    assert second.get() == Decimal("5000")


@pytest.mark.parametrize("amount", ["0", "-5", "NaN", "Infinity"])
def test_an_unusable_balance_is_refused_and_nothing_is_written(tmp_path: Path, amount: str) -> None:
    path = tmp_path / "start.json"
    store = StartBalanceStore(path)
    with pytest.raises(StartBalanceUnreadable):
        store.record_if_missing(Decimal(amount))
    assert not path.exists()


@pytest.mark.parametrize(
    "content",
    ["", "not json", "[]", '{"other": 1}', '{"start_balance": "abc"}', '{"start_balance": "0"}'],
)
def test_a_damaged_file_is_an_error_not_a_reason_to_record_again(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "start.json"
    path.write_text(content, encoding="utf-8")
    store = StartBalanceStore(path)
    with pytest.raises(StartBalanceUnreadable):
        store.get()
    with pytest.raises(StartBalanceUnreadable):
        store.record_if_missing(Decimal("20987.63"))
    assert path.read_text(encoding="utf-8") == content


def test_writing_leaves_no_temporary_files_behind(tmp_path: Path) -> None:
    store = StartBalanceStore(tmp_path / "state" / "start.json")
    store.record_if_missing(Decimal("100"))
    assert sorted(item.name for item in (tmp_path / "state").iterdir()) == ["start.json"]
