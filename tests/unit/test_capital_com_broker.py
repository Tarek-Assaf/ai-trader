"""Offline contract tests for the Capital.com execution adapter."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trader.execution.brokers.base import (
    BrokerError,
    BrokerProtocolError,
    Direction,
    OrderRequest,
    TradingDisabledError,
)
from trader.execution.brokers.capital_com import CapitalComBroker


def position(
    *,
    deal_id: str = "deal-1",
    stop_level: float | None = 99.0,
    level: float = 101.0,
) -> dict[str, object]:
    return {
        "position": {
            "dealId": deal_id,
            "direction": "BUY",
            "size": 1.0,
            "level": level,
            "stopLevel": stop_level,
            "profitLevel": 110.0,
        },
        "market": {"epic": "US100"},
    }


def order() -> OrderRequest:
    return OrderRequest(
        client_order_id="local-001",
        epic="US100",
        direction=Direction.BUY,
        size=Decimal("1"),
        reference_price=Decimal("101"),
        stop_level=Decimal("99"),
        profit_level=Decimal("110"),
    )


class FakeCapitalClient:
    def __init__(self, *, environment: str = "demo") -> None:
        self.environment = environment
        self.calls: list[tuple[str, object]] = []
        self.position_rows: list[Mapping[str, object]] = [position()]
        self.confirm_reply: Mapping[str, object] = {
            "dealStatus": "ACCEPTED",
            "affectedDeals": [{"dealId": "deal-1", "status": "OPENED"}],
        }

    def account_info(self) -> Mapping[str, object]:
        self.calls.append(("account_info", None))
        return {"accounts": []}

    def market_details(self, epic: str) -> Mapping[str, object]:
        self.calls.append(("market_details", epic))
        return {"market": {"epic": epic}}

    def get_positions(self) -> list[Mapping[str, object]]:
        self.calls.append(("get_positions", None))
        return self.position_rows

    def get_working_orders(self) -> list[Mapping[str, object]]:
        self.calls.append(("get_working_orders", None))
        return [{"workingOrderData": {"dealId": "work-1"}}]

    def get_activity(self, start: datetime, end: datetime) -> list[Mapping[str, object]]:
        self.calls.append(("get_activity", (start, end)))
        return [{"dealId": "deal-1", "status": "ACCEPTED"}]

    def open_position(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        self.calls.append(("open_position", payload))
        return {"dealReference": "open-ref"}

    def confirm_deal(self, deal_reference: str) -> Mapping[str, object]:
        self.calls.append(("confirm_deal", deal_reference))
        return self.confirm_reply

    def modify_position(self, deal_id: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        self.calls.append(("modify_position", (deal_id, payload)))
        row = position(stop_level=float(payload["stopLevel"]))
        self.position_rows = [row]
        return {"dealReference": "modify-ref"}

    def close_position(self, deal_id: str) -> Mapping[str, object]:
        self.calls.append(("close_position", deal_id))
        return {"dealReference": "close-ref"}

    def cancel_working_order(self, deal_id: str) -> Mapping[str, object]:
        self.calls.append(("cancel_working_order", deal_id))
        return {"dealReference": "cancel-ref"}


def test_demo_orders_are_disabled_by_default_before_any_request() -> None:
    client = FakeCapitalClient()
    broker = CapitalComBroker(client)

    with pytest.raises(TradingDisabledError, match="disabled"):
        broker.place_order(order())

    assert client.calls == []


def test_live_environment_is_rejected_even_if_order_flag_is_enabled() -> None:
    with pytest.raises(TradingDisabledError, match="demo environment"):
        CapitalComBroker(FakeCapitalClient(environment="live"), enable_demo_orders=True)


def test_order_attaches_stop_and_confirms_open_position() -> None:
    client = FakeCapitalClient()
    broker = CapitalComBroker(client, enable_demo_orders=True)

    receipt = broker.place_order(order())

    assert receipt.client_order_id == "local-001"
    assert receipt.deal_reference == "open-ref"
    assert receipt.deal_id == "deal-1"
    assert receipt.position.stop_level == Decimal("99.0")
    sent = next(value for name, value in client.calls if name == "open_position")
    assert isinstance(sent, Mapping)
    assert sent["guaranteedStop"] is False
    assert sent["stopLevel"] == 99.0
    assert ("confirm_deal", "open-ref") in client.calls


def test_rejected_confirmation_never_claims_open_success() -> None:
    client = FakeCapitalClient()
    client.confirm_reply = {"dealStatus": "REJECTED", "affectedDeals": []}
    broker = CapitalComBroker(client, enable_demo_orders=True)

    with pytest.raises(BrokerProtocolError, match="did not confirm open"):
        broker.place_order(order())

    assert not any(name == "get_positions" for name, _ in client.calls)


def test_missing_confirmed_stop_requests_close_and_never_returns_receipt() -> None:
    client = FakeCapitalClient()
    client.position_rows = [position(stop_level=None)]
    broker = CapitalComBroker(client, enable_demo_orders=True)

    with pytest.raises(BrokerProtocolError, match="close was requested"):
        broker.place_order(order())

    assert any(name == "close_position" for name, _ in client.calls)


def test_ambiguous_open_cannot_be_retried_with_same_client_order_id() -> None:
    client = FakeCapitalClient()
    client.confirm_reply = {
        "dealStatus": "ACCEPTED",
        "affectedDeals": [
            {"dealId": "deal-1", "status": "OPENED"},
            {"dealId": "deal-2", "status": "OPENED"},
        ],
    }
    broker = CapitalComBroker(client, enable_demo_orders=True)

    with pytest.raises(BrokerProtocolError, match="ambiguous"):
        broker.place_order(order())
    request_count = sum(name == "open_position" for name, _ in client.calls)

    with pytest.raises(BrokerError, match="already been submitted"):
        broker.place_order(order())
    assert sum(name == "open_position" for name, _ in client.calls) == request_count == 1


def test_buy_stop_cannot_be_loosened() -> None:
    client = FakeCapitalClient()
    broker = CapitalComBroker(client)

    with pytest.raises(BrokerError, match="may not loosen"):
        broker.modify_position("deal-1", stop_level=Decimal("98"))


def test_modify_close_cancel_and_polling_use_confirmed_references() -> None:
    client = FakeCapitalClient()
    broker = CapitalComBroker(client)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    assert broker.modify_position("deal-1", stop_level=Decimal("100")) == "modify-ref"
    assert broker.close_position("deal-1") == "close-ref"
    assert broker.cancel_working_order("work-1") == "cancel-ref"
    assert broker.get_account()["accounts"] == []
    assert broker.get_market_details("US100")["market"]["epic"] == "US100"
    assert broker.get_positions()[0].deal_id == "deal-1"
    assert broker.get_open_orders()[0]["workingOrderData"]["dealId"] == "work-1"
    assert broker.get_activity(now, now + timedelta(hours=1))[0]["status"] == "ACCEPTED"


class _HttpError(RuntimeError):
    def __init__(self, status_code, error_code=None):
        self.status_code, self.error_code = status_code, error_code
        super().__init__(f"HTTP {status_code}")


def test_a_refused_order_request_is_a_rejection_not_an_ambiguous_outcome() -> None:
    from trader.execution.brokers.base import OrderRejected

    client = FakeCapitalClient()

    def refuse(_payload):
        raise _HttpError(400, "error.invalid.stoploss.minvalue")

    client.open_position = refuse
    broker = CapitalComBroker(client, enable_demo_orders=True)

    with pytest.raises(OrderRejected, match="error.invalid.stoploss.minvalue"):
        broker.place_order(order())


def test_a_server_error_on_the_order_request_stays_ambiguous() -> None:
    from trader.execution.brokers.base import OrderRejected

    client = FakeCapitalClient()

    def fail(_payload):
        raise _HttpError(503)

    client.open_position = fail
    broker = CapitalComBroker(client, enable_demo_orders=True)

    with pytest.raises(_HttpError):
        broker.place_order(order())
    try:
        broker.place_order(order())
    except OrderRejected:
        raise AssertionError("a 5xx must not be treated as a clean rejection") from None
    except Exception:
        pass
