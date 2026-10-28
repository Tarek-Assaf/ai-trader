"""Real Capital.com demo execution: account view, netting partial close and safeguards."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from trader.execution.brokers.base import BrokerError, Direction, OrderRequest
from trader.execution.brokers.capital_com import CapitalComBroker
from trader.execution.brokers.capital_runtime import CapitalRuntimeBroker
from trader.execution.reconciliation import CapitalAccountFieldMap

FIELDS = CapitalAccountFieldMap("balance", "balance", "profitLoss", "derived", "available", True)


class FakeCapital:
    """Minimal Capital.com demo: one account, market orders, netting when hedging is off."""

    environment = "demo"

    def __init__(self, *, netting: bool = True) -> None:
        self.netting = netting
        self.positions: dict[str, dict[str, Any]] = {}
        self.balance = Decimal("1000")
        self.profit_loss = Decimal("0")
        self.available = Decimal("1000")
        self._counter = 0
        self._deals: dict[str, dict[str, Any]] = {}

    def account_info(self) -> dict[str, Any]:
        return {
            "accounts": [
                {
                    "accountId": "A1",
                    "currency": "USD",
                    "preferred": True,
                    "balance": {
                        "balance": float(self.balance),
                        "deposit": 1000.0,
                        "profitLoss": float(self.profit_loss),
                        "available": float(self.available),
                    },
                }
            ]
        }

    def market_details(self, epic: str) -> dict[str, Any]:
        return {"instrument": {"epic": epic}}

    def get_positions(self) -> list[dict[str, Any]]:
        return [
            {
                "position": {
                    "dealId": deal_id,
                    "direction": p["direction"],
                    "size": float(p["size"]),
                    "level": float(p["level"]),
                    "stopLevel": p.get("stop"),
                    "profitLevel": p.get("limit"),
                },
                "market": {"epic": p["epic"]},
            }
            for deal_id, p in self.positions.items()
        ]

    def get_working_orders(self) -> list[dict[str, Any]]:
        return []

    def get_activity(self, start, end) -> list[dict[str, Any]]:
        return []

    def open_position(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._counter += 1
        reference = f"o_{self._counter}"
        size = Decimal(str(payload["size"]))
        direction = payload["direction"]
        opposite = [
            (deal_id, p)
            for deal_id, p in self.positions.items()
            if p["epic"] == payload["epic"] and p["direction"] != direction
        ]
        if self.netting and opposite:
            deal_id, held = opposite[0]
            held["size"] -= size
            if held["size"] <= 0:
                del self.positions[deal_id]
            self._deals[reference] = {"status": "AMENDED", "dealId": deal_id}
        else:
            deal_id = f"deal-{self._counter}"
            self.positions[deal_id] = {
                "epic": payload["epic"],
                "direction": direction,
                "size": size,
                "level": Decimal("100"),
                "stop": payload.get("stopLevel"),
                "limit": payload.get("profitLevel"),
            }
            self._deals[reference] = {"status": "OPENED", "dealId": deal_id}
        return {"dealReference": reference}

    def confirm_deal(self, reference: str) -> dict[str, Any]:
        deal = self._deals[reference]
        return {"dealStatus": "ACCEPTED", "affectedDeals": [dict(deal)]}

    def close_position(self, deal_id: str) -> dict[str, Any]:
        self._counter += 1
        self.positions.pop(deal_id, None)
        reference = f"c_{self._counter}"
        self._deals[reference] = {"status": "CLOSED", "dealId": deal_id}
        return {"dealReference": reference}

    def modify_position(self, deal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._counter += 1
        self.positions[deal_id]["stop"] = payload.get("stopLevel")
        if "profitLevel" in payload:
            self.positions[deal_id]["limit"] = payload["profitLevel"]
        reference = f"m_{self._counter}"
        self._deals[reference] = {"status": "AMENDED", "dealId": deal_id}
        return {"dealReference": reference}


def _order(size: str = "0.004") -> OrderRequest:
    return OrderRequest(
        "cycle-1",
        "US100",
        Direction.BUY,
        Decimal(size),
        Decimal("100"),
        Decimal("90"),
        Decimal("130"),
    )


def _runtime(client: FakeCapital) -> CapitalRuntimeBroker:
    return CapitalRuntimeBroker(client, FIELDS, contract_size=lambda _epic: Decimal("1"))


def test_account_is_normalized_with_the_verified_mapping() -> None:
    client = FakeCapital()
    client.balance, client.profit_loss, client.available = (
        Decimal("1010"),
        Decimal("10"),
        Decimal("1000"),
    )
    runtime = _runtime(client)
    runtime.place_order(_order("0.1"))
    account = runtime.get_account()
    assert account["equity"] == Decimal("1010")  # balance already includes unrealised P&L
    assert account["balance"] == Decimal("1000")  # cash = balance - profitLoss
    assert account["margin_used"] == Decimal("10")  # balance - available
    assert account["notional_exposure"] == Decimal("100") * Decimal("0.1")
    assert account["currency"] == "USD"


def test_partial_close_nets_into_the_open_position() -> None:
    client = FakeCapital()
    runtime = _runtime(client)
    receipt = runtime.place_order(_order("0.004"))
    runtime.close_partial(receipt.deal_id, Decimal("0.001"))
    (position,) = runtime.get_positions()
    assert position.deal_id == receipt.deal_id
    assert position.size == Decimal("0.003")
    assert position.stop_level == Decimal("90")


def test_partial_close_that_would_hedge_is_undone_and_refused() -> None:
    client = FakeCapital(netting=False)
    runtime = _runtime(client)
    receipt = runtime.place_order(_order("0.004"))
    with pytest.raises(BrokerError):
        runtime.close_partial(receipt.deal_id, Decimal("0.001"))
    (position,) = runtime.get_positions()  # the stray opposite position was closed again
    assert position.deal_id == receipt.deal_id
    assert position.size == Decimal("0.004")


def test_partial_close_rejects_a_size_that_is_not_smaller_than_the_position() -> None:
    client = FakeCapital()
    runtime = _runtime(client)
    receipt = runtime.place_order(_order("0.002"))
    with pytest.raises(ValueError):
        runtime.close_partial(receipt.deal_id, Decimal("0.002"))
    with pytest.raises(ValueError):
        runtime.close_partial(receipt.deal_id, Decimal("0"))


def test_partial_close_is_disabled_unless_demo_orders_are_enabled() -> None:
    from trader.execution.brokers.base import TradingDisabledError

    client = FakeCapital()
    broker = CapitalComBroker(client)  # orders disabled
    with pytest.raises(TradingDisabledError):
        broker.close_partial("deal-1", Decimal("0.001"))


def test_runtime_broker_refuses_an_unverified_mapping_and_an_ambiguous_account() -> None:
    unverified = CapitalAccountFieldMap(
        "balance", "balance", "profitLoss", "derived", "available", False
    )
    with pytest.raises(ValueError):
        CapitalRuntimeBroker(FakeCapital(), unverified, contract_size=lambda _e: Decimal("1"))

    client = FakeCapital()
    original = client.account_info

    def two_accounts() -> dict[str, Any]:
        payload = original()
        second = dict(payload["accounts"][0], accountId="A2", preferred=False)
        payload["accounts"][0]["preferred"] = False
        payload["accounts"].append(second)
        return payload

    client.account_info = two_accounts  # type: ignore[method-assign]
    with pytest.raises(BrokerError):
        _runtime(client).get_account()


def test_capital_execution_switch_needs_demo_paper_and_the_env_flag(monkeypatch) -> None:
    from types import SimpleNamespace

    from trader.api.app import _capital_execution_enabled

    demo_paper = SimpleNamespace(broker_env="demo", trading_mode="paper")
    monkeypatch.delenv("EXECUTION_BROKER", raising=False)
    assert not _capital_execution_enabled(demo_paper)
    monkeypatch.setenv("EXECUTION_BROKER", "capital_demo")
    assert _capital_execution_enabled(demo_paper)
    assert not _capital_execution_enabled(SimpleNamespace(broker_env="live", trading_mode="paper"))
    assert not _capital_execution_enabled(SimpleNamespace(broker_env="demo", trading_mode="auto"))
