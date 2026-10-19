from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trader.execution.brokers.base import Direction, OrderReceipt, OrderRequest, Position
from trader.execution.control import ControlMode
from trader.experiments.tai_demo import (
    ClosedBar,
    TaiDemoRefused,
    TaiDemoRunner,
    TaiDemoSettings,
    TaiSignal,
)


class Control:
    def __init__(self, mode=ControlMode.RUN):
        self.mode = mode

    def read(self):
        return self.mode


class Gateway:
    def __init__(self):
        self.positions = []
        self.partial = []
        self.modified = []
        self.closed = []

    def get_positions(self):
        return tuple(self.positions)

    def modify_position(self, deal_id, *, stop_level, profit_level=None):
        self.modified.append((deal_id, stop_level, profit_level))
        return "modified"

    def close_position(self, deal_id):
        self.closed.append(deal_id)
        self.positions = [p for p in self.positions if p.deal_id != deal_id]
        return "closed"

    def close_partial(self, deal_id, size):
        self.partial.append((deal_id, size))
        return "partial"


class Manager:
    def __init__(self, gateway, veto=False):
        self.gateway = gateway
        self.veto = veto
        self.submits = []

    def submit(self, cycle_id, order):
        self.submits.append((cycle_id, order))
        if self.veto:
            return None
        position = Position(
            "deal-1",
            order.epic,
            order.direction,
            order.size,
            order.reference_price,
            order.stop_level,
            order.profit_level,
        )
        self.gateway.positions.append(position)
        return OrderReceipt(cycle_id, "ref-1", position.deal_id, position)


def bar(price="100", *, high="100", low="100", closed=True, market_open=True, in_break=False):
    return ClosedBar(
        datetime(2026, 10, 19, 8, 0, tzinfo=UTC),
        "NSXUSD",
        Decimal(price), Decimal(high), Decimal(low), Decimal(price),
        closed=closed, market_open=market_open, in_break=in_break,
    )


def runner(gateway, control=None, veto=False, journal=None, alerts=None):
    manager = Manager(gateway, veto=veto)
    signal = TaiSignal(Direction.BUY, Decimal("100"), Decimal("99"))
    return TaiDemoRunner(
        settings=TaiDemoSettings("demo", "paper", False, True),
        order_manager=manager,
        position_gateway=gateway,
        signal_provider=lambda _bar: signal,
        order_factory=lambda signal, cycle_id: OrderRequest(
            cycle_id,
            "NSXUSD",
            signal.direction,
            Decimal("3"),
            signal.entry,
            signal.stop,
            signal.levels()[2],
        ),
        control=control or Control(),
        journal=journal,
        alert=(alerts.append if alerts is not None else None),
    ), manager


def test_three_flag_guard_refuses_every_unsafe_combination():
    for values in (
        ("live", "paper", False),
        ("demo", "auto", False),
        ("demo", "paper", True),
        ("demo", "paper", False),
    ):
        with pytest.raises(TaiDemoRefused):
            TaiDemoRunner(
                settings=TaiDemoSettings(*values, False),
                order_manager=object(), position_gateway=object(), signal_provider=lambda _: None,
                order_factory=lambda *_: None, control=Control(),
            )


def test_entry_routes_through_order_manager_and_enforces_one_position():
    gateway = Gateway()
    runner_instance, manager = runner(gateway)
    first = runner_instance.on_bar(bar())
    second = runner_instance.on_bar(bar())
    assert first.action == "SUBMITTED"
    assert second.reason == "one_position_rule"
    assert len(manager.submits) == 1


def test_guardian_veto_and_market_closed_are_journaled_without_entry():
    events = []
    gateway = Gateway()
    veto_runner, manager = runner(gateway, veto=True, journal=events.append)
    assert veto_runner.on_bar(bar()).reason == "guardian_veto"
    assert len(manager.submits) == 1
    closed = runner(Gateway(), journal=events.append)[0]
    assert closed.on_bar(bar(closed=False)).reason == "bar_not_closed"
    assert any(event["event"] == "guardian_veto" for event in events)
    blocked = runner(Gateway())[0]
    assert blocked.on_bar(bar(market_open=False)).reason == "market_closed_or_blackout"


def test_kill_flattens_and_alerts():
    gateway = Gateway()
    control = Control(ControlMode.KILL)
    alerts = []
    runner_instance, _ = runner(gateway, control=control, alerts=alerts)
    runner_instance.on_bar(bar())
    assert alerts and "kill switch" in alerts[0]


def test_scale_out_and_stop_to_entry_bookkeeping():
    gateway = Gateway()
    runner_instance, _ = runner(gateway)
    runner_instance.on_bar(bar())
    assert runner_instance.on_bar(bar(high="101")).reason == "one_position_rule"
    assert gateway.partial == [("deal-1", Decimal("1"))]
    assert gateway.modified == [("deal-1", Decimal("100"), Decimal("103"))]
    runner_instance.on_bar(bar(high="102"))
    assert gateway.partial[-1] == ("deal-1", Decimal("1"))
    runner_instance.on_bar(bar(high="103"))
    assert gateway.closed == ["deal-1"]
