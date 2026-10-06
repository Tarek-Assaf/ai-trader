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
    profit_loss: str = "profitLoss"
    margin_used: str = "deposit"
    available: str = "available"
    verified: bool = False

    def __post_init__(self) -> None:
        fields = {"balance", "deposit", "profitLoss", "available"}
        if self.cash_balance not in fields or self.equity not in fields | {"derived"}:
            raise ValueError("unsupported Capital.com account field mapping")
        if (
            self.profit_loss not in fields
            or self.margin_used not in fields | {"derived"}
            or self.available not in fields
        ):
            raise ValueError("unsupported Capital.com account field mapping")
        if self.cash_balance == "derived":
            raise ValueError("the raw balance field is required to derive cash")

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> CapitalAccountFieldMap:
        """Build a map from checked-in config without silently accepting omissions."""
        required = {"balance", "equity", "profit_loss", "margin_used", "available", "verified"}
        if set(mapping) != required:
            raise ValueError("capital account mapping must contain exactly the declared fields")
        names = ("balance", "equity", "profit_loss", "margin_used", "available")
        strings: list[str] = []
        for key in names:
            value = mapping[key]
            if not isinstance(value, str):
                raise ValueError("capital account mapping field names must be strings")
            strings.append(value)
        verified = mapping["verified"]
        if not isinstance(verified, bool):
            raise ValueError("capital account mapping verified flag must be boolean")
        return cls(strings[0], strings[1], strings[2], strings[3], strings[4], verified)

    def require_verified(self) -> None:
        if not self.verified:
            raise ValueError("Capital.com account mapping is an unverified hypothesis")


@dataclass(frozen=True, slots=True)
class CapitalAccountSnapshot:
    """Normalized Capital.com account values, including free funds."""

    account_id: str
    currency: str
    balance: Decimal
    cash: Decimal
    profit_loss: Decimal
    equity: Decimal
    margin_used: Decimal
    available: Decimal
    notional_exposure: Decimal
    mapping_verified: bool

    def to_broker_figures(self) -> BrokerAccountFigures:
        return BrokerAccountFigures(
            balance=self.balance,
            equity=self.equity,
            currency=self.currency,
            margin_used=self.margin_used,
            notional_exposure=self.notional_exposure,
        )


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
    margin_used: Decimal | None = None,
    notional_exposure: Decimal,
) -> BrokerAccountFigures:
    """Normalize the active account using an explicit map and valuation inputs."""
    figures = normalize_capital_account_snapshot(
        response,
        account_id=account_id,
        fields=fields,
        notional_exposure=notional_exposure,
        require_verified=False,
    ).to_broker_figures()
    if margin_used is not None:
        return replace(figures, margin_used=margin_used)
    return figures


def normalize_capital_account_snapshot(
    response: Mapping[str, object],
    *,
    account_id: str,
    fields: CapitalAccountFieldMap,
    notional_exposure: Decimal,
    require_verified: bool = True,
) -> CapitalAccountSnapshot:
    """Select the account and derive equity, cash and margin through the declared map."""
    if require_verified:
        fields.require_verified()
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
    balance = _number(balances.get(fields.cash_balance))
    profit_loss = _number(balances.get(fields.profit_loss))
    equity = (
        balance + profit_loss
        if fields.equity == "derived"
        else _number(balances.get(fields.equity))
    )
    available = _number(balances.get(fields.available))
    if available < 0:
        raise ValueError("account available must be non-negative")
    cash = balance - profit_loss
    margin_used = (
        balance - available
        if fields.margin_used == "derived"
        else _number(balances.get(fields.margin_used))
    )
    deposit = _number(balances.get("deposit"))
    if balance == 0 and profit_loss == -deposit:
        raise ValueError("invalid anomalous Capital.com account snapshot")
    if margin_used < 0:
        raise ValueError("derived Capital.com margin must be non-negative")
    return CapitalAccountSnapshot(
        account_id=account_id,
        currency=currency,
        balance=balance,
        cash=cash,
        profit_loss=profit_loss,
        equity=equity,
        margin_used=margin_used,
        available=available,
        notional_exposure=_number(notional_exposure),
        mapping_verified=fields.verified,
    )


def capital_account_field_map_from_config(config: Mapping[str, object]) -> CapitalAccountFieldMap:
    """Read the settings section and fail closed until verification is recorded."""
    section = config.get("capital_account_mapping")
    if not isinstance(section, Mapping):
        raise ValueError("capital_account_mapping config is required")
    fields = CapitalAccountFieldMap.from_mapping(section)
    fields.require_verified()
    return fields


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
