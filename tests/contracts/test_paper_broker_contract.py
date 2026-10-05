"""BrokerAdapter conformance and execution-cost tests for PaperBroker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trader.execution.brokers import base as broker_base, paper as paper_broker

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _as_broker_adapter(broker: broker_base.BrokerAdapter) -> broker_base.BrokerAdapter:
    """Static structural check that paper_broker.PaperBroker implements the shared contract."""
    return broker


def make_broker(*, clock=None) -> paper_broker.PaperBroker:
    broker = paper_broker.PaperBroker(
        starting_cash=Decimal("1000"),
        currency="USD",
        costs=paper_broker.PaperExecutionCosts(
            spread_points={"US100": Decimal("2")},
            slippage_points={"US100": Decimal("0.5")},
        ),
        clock=clock or (lambda: NOW),
    )
    _as_broker_adapter(broker)
    return broker


def make_order(
    *,
    direction: broker_base.Direction = broker_base.Direction.BUY,
    client_order_id: str = "cycle-1",
    size: Decimal = Decimal("2"),
) -> broker_base.OrderRequest:
    if direction is broker_base.Direction.BUY:
        stop, target = Decimal("95"), Decimal("110")
    else:
        stop, target = Decimal("105"), Decimal("90")
    return broker_base.OrderRequest(
        client_order_id=client_order_id,
        epic="US100",
        direction=direction,
        size=size,
        reference_price=Decimal("100"),
        stop_level=stop,
        profit_level=target,
    )


def test_paper_broker_satisfies_adapter_contract_and_models_costs() -> None:
    broker = make_broker()

    assert broker.get_account() == {
        "balance": Decimal("1000"),
        "equity": Decimal("1000"),
        "profit": Decimal("0"),
        "currency": "USD",
        "financing": Decimal("0"),
        "financing_status": "placeholder_not_modeled",
    }
    assert broker.get_market_details("US100")["cost_source"] == "explicit_paper_execution_costs"
    assert broker.get_open_orders() == ()

    receipt = broker.place_order(make_order())
    assert receipt.deal_reference == "paper-open-00000001"
    assert receipt.position.level == Decimal("101.5")  # half-spread plus adverse slippage
    assert receipt.position.stop_level == Decimal("95")
    assert receipt.position.profit_level == Decimal("110")
    assert broker.get_positions() == (receipt.position,)

    start = NOW - timedelta(minutes=1)
    end = NOW + timedelta(minutes=1)
    events = broker.get_activity(start, end)
    assert len(events) == 1
    assert events[0]["action"] == "opened"
    assert events[0]["financing_status"] == "placeholder_not_modeled"

    broker.modify_position(receipt.deal_id, stop_level=Decimal("96"))
    assert broker.get_positions()[0].stop_level == Decimal("96")
    with pytest.raises(broker_base.BrokerError, match="may not loosen"):
        broker.modify_position(receipt.deal_id, stop_level=Decimal("95"))

    broker.update_quote("US100", bid=Decimal("104"), ask=Decimal("106"))
    partial_ref = broker.close_partial(receipt.deal_id, Decimal("1"))
    assert partial_ref.startswith("paper-partial-")
    assert broker.get_positions()[0].size == Decimal("1")
    close_ref = broker.close_position(receipt.deal_id)
    assert close_ref.startswith("paper-close-")
    assert broker.get_positions() == ()
    assert broker.get_open_orders() == ()
    account = broker.get_account()
    # Entry at 101.5; each close exits at bid minus slippage (103.5).
    assert account["balance"] == Decimal("1004.0")
    assert account["equity"] == Decimal("1004.0")
    assert len(broker.get_activity(start, end)) == 4


@pytest.mark.parametrize(
    ("direction", "bid", "ask", "reason", "exit_level"),
    [
        (broker_base.Direction.BUY, Decimal("94"), Decimal("96"), "stop_loss", Decimal("93.5")),
        (broker_base.Direction.BUY, Decimal("111"), Decimal("113"), "take_profit", Decimal("110.5")),
        (broker_base.Direction.SELL, Decimal("104"), Decimal("106"), "stop_loss", Decimal("106.5")),
        (broker_base.Direction.SELL, Decimal("89"), Decimal("91"), "take_profit", Decimal("91.5")),
    ],
)
def test_attached_brackets_trigger_on_executable_side(
    direction: broker_base.Direction,
    bid: Decimal,
    ask: Decimal,
    reason: str,
    exit_level: Decimal,
) -> None:
    broker = make_broker()
    receipt = broker.place_order(make_order(direction=direction))

    closed = broker.update_quote("US100", bid=bid, ask=ask)

    assert closed == (receipt.deal_id,)
    assert broker.get_positions() == ()
    event = broker.get_activity(NOW, NOW)[-1]
    assert event["reason"] == reason
    assert event["level"] == exit_level


def test_sell_fill_applies_half_spread_and_adverse_slippage() -> None:
    receipt = make_broker().place_order(make_order(direction=broker_base.Direction.SELL))
    assert receipt.position.level == Decimal("98.5")


def test_quote_fill_uses_executable_bid_or_ask_without_double_counting_spread() -> None:
    broker = make_broker()
    broker.update_quote("US100", bid=Decimal("99"), ask=Decimal("103"))

    buy = broker.place_order(make_order(client_order_id="buy"))
    assert buy.position.level == Decimal("103.5")

    sell = broker.place_order(make_order(direction=broker_base.Direction.SELL, client_order_id="sell"))
    assert sell.position.level == Decimal("98.5")


def test_duplicate_order_id_is_rejected_without_second_position() -> None:
    broker = make_broker()
    broker.place_order(make_order())

    with pytest.raises(broker_base.BrokerError, match="already been submitted"):
        broker.place_order(make_order())

    assert len(broker.get_positions()) == 1


def test_missing_cost_configuration_fails_closed() -> None:
    broker = paper_broker.PaperBroker(
        starting_cash=Decimal("1000"),
        currency="USD",
        costs=paper_broker.PaperExecutionCosts(
            spread_points={"GOLD": Decimal("1")},
            slippage_points={"GOLD": Decimal("0.2")},
        ),
        clock=lambda: NOW,
    )

    with pytest.raises(broker_base.BrokerProtocolError, match="not configured"):
        broker.place_order(make_order())

    assert broker.get_positions() == ()


def test_invalid_quotes_and_activity_ranges_are_rejected() -> None:
    broker = make_broker()
    with pytest.raises(ValueError, match="quote"):
        broker.update_quote("US100", bid=Decimal("2"), ask=Decimal("1"))
    with pytest.raises(ValueError, match="activity range"):
        broker.get_activity(NOW, NOW - timedelta(seconds=1))


def test_stop_wins_if_a_gap_crosses_both_bracket_levels() -> None:
    broker = make_broker()
    receipt = broker.place_order(make_order())

    assert broker.update_quote("US100", bid=Decimal("90"), ask=Decimal("112")) == (receipt.deal_id,)
    event = broker.get_activity(NOW, NOW)[-1]
    assert event["reason"] == "stop_loss"


def test_cost_model_rejects_unpaired_or_negative_inputs() -> None:
    with pytest.raises(ValueError, match="same epics"):
        paper_broker.PaperExecutionCosts(
            spread_points={"US100": Decimal("2")},
            slippage_points={"GOLD": Decimal("0.5")},
        )
    with pytest.raises(ValueError, match="non-negative"):
        paper_broker.PaperExecutionCosts(
            spread_points={"US100": Decimal("2")},
            slippage_points={"US100": Decimal("-0.5")},
        )
