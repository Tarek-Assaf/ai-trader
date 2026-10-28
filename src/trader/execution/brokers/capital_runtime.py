"""Runtime view of the Capital.com demo broker.

The runtime (order manager, Guardian context, loss guard, reconciler) reads a flat account
mapping and a few extra methods. :class:`CapitalComBroker` returns Capital.com's raw
payload, so this wrapper normalizes it with the owner-verified account mapping (V-178)
and delegates everything else, including the netting-based partial close.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from trader.execution.brokers.base import (
    BrokerError,
    OrderReceipt,
    OrderRequest,
    Position,
)
from trader.execution.brokers.capital_com import CapitalComBroker
from trader.execution.reconciliation import (
    CapitalAccountFieldMap,
    normalize_capital_account_snapshot,
)


class CapitalRuntimeBroker:
    """Demo-only broker with the flat account view the runtime expects."""

    def __init__(
        self,
        client: Any,
        fields: CapitalAccountFieldMap,
        *,
        contract_size: Callable[[str], Decimal],
    ) -> None:
        fields.require_verified()
        self._client = client
        self._fields = fields
        self._contract_size = contract_size
        self._broker = CapitalComBroker(client, enable_demo_orders=True)

    def get_account(self) -> Mapping[str, object]:
        response = self._client.account_info()
        accounts = response.get("accounts") if isinstance(response, Mapping) else None
        if not isinstance(accounts, list):
            raise BrokerError("Capital.com account list is unavailable")
        usable = [row for row in accounts if isinstance(row, Mapping) and row.get("accountId")]
        if len(usable) != 1:
            preferred = [row for row in usable if row.get("preferred") is True]
            if len(preferred) != 1:
                raise BrokerError("the active Capital.com account is ambiguous")
            usable = preferred
        account_id = str(usable[0]["accountId"])
        notional = sum(
            (
                abs(position.level * position.size * self._contract_size(position.epic))
                for position in self._broker.get_positions()
            ),
            start=Decimal("0"),
        )
        try:
            snapshot = normalize_capital_account_snapshot(
                response,
                account_id=account_id,
                fields=self._fields,
                notional_exposure=notional,
                reread=self._client.account_info,
            )
        except ValueError as exc:
            raise BrokerError(f"Capital.com account figures unavailable: {exc}") from None
        return {
            "balance": snapshot.cash,
            "equity": snapshot.equity,
            "profitLoss": snapshot.profit_loss,
            "available": snapshot.available,
            "currency": snapshot.currency,
            "margin_used": snapshot.margin_used,
            "notional_exposure": snapshot.notional_exposure,
        }

    def get_positions(self) -> Sequence[Position]:
        return self._broker.get_positions()

    def get_open_orders(self) -> Sequence[Mapping[str, object]]:
        return self._broker.get_open_orders()

    def get_working_orders(self) -> Sequence[Mapping[str, object]]:
        return self._broker.get_working_orders()

    def get_market_details(self, epic: str) -> Mapping[str, object]:
        return self._broker.get_market_details(epic)

    def get_activity(self, start: datetime, end: datetime) -> Sequence[Mapping[str, object]]:
        return self._broker.get_activity(start, end)

    def place_order(self, order: OrderRequest) -> OrderReceipt:
        return self._broker.place_order(order)

    def modify_position(
        self, deal_id: str, *, stop_level: Decimal, profit_level: Decimal | None = None
    ) -> str:
        return self._broker.modify_position(
            deal_id, stop_level=stop_level, profit_level=profit_level
        )

    def close_position(self, deal_id: str) -> str:
        return self._broker.close_position(deal_id)

    def close_partial(self, deal_id: str, size: Decimal) -> str:
        return self._broker.close_partial(deal_id, size)

    def cancel_working_order(self, deal_id: str) -> str:
        return self._broker.cancel_working_order(deal_id)
