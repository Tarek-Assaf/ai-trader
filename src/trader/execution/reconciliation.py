"""Explicit account normalization and fail-closed P&L reconciliation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from trader.execution.journal import ExecutionJournal
from trader.execution.pnl import BrokerAccountFigures, PnlEngine, PnlReport, PnlTrade


@dataclass(frozen=True, slots=True)
class CapitalAccountFieldMap:
    """Required mapping validated against the demo account before runtime use.

    The public API lists balance/deposit/profitLoss/available but does not
    define their accounting semantics. Never silently guess which is cash or
    equity. Margin and account-currency exposure must be supplied explicitly
    from validated position/account valuation, not defaulted to zero.
    """

    cash_balance: str
    equity: str

    def __post_init__(self) -> None:
        fields = {"balance", "deposit", "profitLoss", "available"}
        if self.cash_balance not in fields or self.equity not in fields:
            raise ValueError("unsupported Capital.com account field mapping")
        if self.cash_balance == self.equity:
            raise ValueError("cash and equity need independently validated fields")


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int | float | Decimal):
        raise ValueError("account figure is missing or invalid")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("account figure is invalid") from None
    if not result.is_finite():
        raise ValueError("account figure is not finite")
    return result


def normalize_capital_account(
    response: Mapping[str, object],
    *,
    account_id: str,
    fields: CapitalAccountFieldMap,
    margin_used: Decimal,
    notional_exposure: Decimal,
) -> BrokerAccountFigures:
    """Select the explicit active account; reject absent or duplicate rows."""
    accounts = response.get("accounts")
    if not account_id or not isinstance(accounts, list):
        raise ValueError("active account id and account list are required")
    selected = [
        row for row in accounts if isinstance(row, Mapping) and row.get("accountId") == account_id
    ]
    if len(selected) != 1:
        raise ValueError("active account is missing or duplicated")
    account = selected[0]
    balances = account.get("balance")
    currency = account.get("currency")
    if not isinstance(balances, Mapping) or not isinstance(currency, str):
        raise ValueError("account balances or currency are missing")
    return BrokerAccountFigures(
        balance=_number(balances.get(fields.cash_balance)),
        equity=_number(balances.get(fields.equity)),
        currency=currency,
        margin_used=margin_used,
        notional_exposure=notional_exposure,
    )


def reconcile_pnl(
    trades: Sequence[PnlTrade],
    journal: ExecutionJournal,
    *,
    starting_balance: Decimal,
    currency: str,
    broker: BrokerAccountFigures,
    tolerance: Decimal = Decimal("0.01"),
) -> PnlReport:
    """Use the persisted initial stop even after a broker stop is tightened.

    Raises on missing reservations, missing valuations, arithmetic errors, or
    reconciliation deltas. OrderManager must catch this and pause plus alert.
    """
    records = {record.get("deal_id"): record for record in journal.records()}
    normalized: list[PnlTrade] = []
    for trade in trades:
        record = records.get(trade.trade_id)
        if record is None:
            raise ValueError("trade has no durable initial-stop record")
        normalized.append(replace(trade, stop_level=Decimal(record["initial_stop"])))
    report = PnlEngine.calculate(
        normalized,
        starting_balance=starting_balance,
        currency=currency,
        broker=broker,
        as_of=datetime.now(UTC),
        reconciliation_tolerance=tolerance,
    )
    if report.reconciliation.alert_required:
        raise ValueError("account P&L reconciliation exceeds tolerance")
    return report
