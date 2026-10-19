from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trader.execution.brokers.base import Direction, OrderReceipt, OrderRequest, Position
from trader.execution.control import ControlMode
from trader.experiments.tai_demo import (
    ClosedBar,
    TaiDemoRefused,
    TaiDemoRunner,
    TaiDemoSettings,
    TaiInstrumentRules,
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
        Decimal(price),
        Decimal(high),
        Decimal(low),
        Decimal(price),
        closed=closed,
        market_open=market_open,
        in_break=in_break,
    )


def runner(gateway, control=None, veto=False, journal=None, alerts=None, **kwargs):
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
        **kwargs,
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
                order_manager=object(),
                position_gateway=object(),
                signal_provider=lambda _: None,
                order_factory=lambda *_: None,
                control=Control(),
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


def test_runtime_rebuild_restores_open_position_management():
    gateway = Gateway()
    first, _ = runner(gateway)
    assert first.on_bar(bar()).action == "SUBMITTED"

    second, manager = runner(gateway)
    restored = TaiDemoRunner(
        settings=second.settings,
        order_manager=manager,
        position_gateway=gateway,
        signal_provider=second._signal,
        order_factory=second._order_factory,
        control=second._control,
        managed_state=first.snapshot_state(),
    )
    assert restored.on_bar(bar()).reason == "one_position_rule"
    assert manager.submits == []
    restored.on_bar(bar(high="103"))
    assert gateway.closed == ["deal-1"]


def _sizing_runner(balance, start="1000"):
    gateway = Gateway()
    manager = Manager(gateway)
    return TaiDemoRunner(
        settings=TaiDemoSettings("demo", "paper", False, True, start_balance=Decimal(start)),
        order_manager=manager,
        position_gateway=gateway,
        signal_provider=lambda _bar: None,
        order_factory=lambda *_: None,
        control=Control(),
        balance_provider=lambda: Decimal(balance),
    )


def test_lot_is_base_at_start_and_grows_proportionally_with_balance():
    rules = TaiInstrumentRules(Decimal("0.01"), Decimal("0.01"))
    assert _sizing_runner("1000")._requested_size(rules) == Decimal("0.10")
    assert _sizing_runner("1100")._requested_size(rules) == Decimal("0.11")
    assert _sizing_runner("1500")._requested_size(rules) == Decimal("0.15")
    # start 10,999.80 (owner demo) must trade exactly 0.1 at the start
    assert _sizing_runner("10999.80", "10999.80")._requested_size(rules) == Decimal("0.10")


def test_lot_never_below_base_and_rounds_down_to_step():
    rules = TaiInstrumentRules(Decimal("0.01"), Decimal("0.05"))
    assert _sizing_runner("500")._requested_size(rules) == Decimal("0.10")
    assert _sizing_runner("1190")._requested_size(rules) == Decimal("0.10")
    assert _sizing_runner("1200")._requested_size(rules) == Decimal("0.10")
    assert _sizing_runner("1500")._requested_size(rules) == Decimal("0.15")


def test_enabled_experiment_without_start_balance_is_refused():
    class Cfg:
        broker_env = "demo"
        trading_mode = "paper"
        live_trading_enabled = False
        tai_demo_experiment = True
        tai_demo_base_lot = "0.1"
        tai_demo_start_balance = "0"

    with pytest.raises(TaiDemoRefused):
        TaiDemoSettings.from_settings(Cfg())


def test_persisted_position_restores_management_and_prevents_duplicate_entry(tmp_path: Path):
    state_path = tmp_path / "state" / "tai.json"
    gateway = Gateway()
    first, _ = runner(gateway, state_path=state_path, epic="NSXUSD")
    assert first.on_bar(bar()).action == "SUBMITTED"
    restored, manager = runner(gateway, state_path=state_path, epic="NSXUSD")
    assert restored.on_bar(bar()).reason == "one_position_rule"
    assert manager.submits == []
    restored.on_bar(bar(high="103"))
    assert gateway.closed == ["deal-1"]


def test_missing_broker_position_is_dropped_and_journaled(tmp_path: Path):
    state_path = tmp_path / "tai.json"
    gateway = Gateway()
    first, _ = runner(gateway, state_path=state_path, epic="NSXUSD")
    assert first.on_bar(bar()).action == "SUBMITTED"
    gateway.positions.clear()
    events = []
    restored, manager = runner(gateway, state_path=state_path, epic="NSXUSD", journal=events.append)
    assert any(event["event"] == "reconcile_closed" for event in events)
    assert restored.on_bar(bar()).action == "SUBMITTED"
    assert len(manager.submits) == 1


def test_unknown_broker_position_refuses_entries_and_alerts(tmp_path: Path):
    gateway = Gateway()
    gateway.positions.append(
        Position(
            "unknown",
            "NSXUSD",
            Direction.BUY,
            Decimal("1"),
            Decimal("100"),
            Decimal("99"),
            Decimal("103"),
        )
    )
    alerts = []
    instance, manager = runner(
        gateway, state_path=tmp_path / "tai.json", epic="NSXUSD", alerts=alerts
    )
    assert instance.on_bar(bar()).reason == "one_position_rule"
    assert manager.submits == []
    assert any("unmanaged" in alert for alert in alerts)


def test_corrupt_state_fails_closed_without_entry(tmp_path: Path):
    state_path = tmp_path / "tai.json"
    state_path.write_text("not-json", encoding="utf-8")
    alerts = []
    instance, manager = runner(Gateway(), state_path=state_path, epic="NSXUSD", alerts=alerts)
    assert instance.on_bar(bar()).reason == "startup_reconciliation_failed"
    assert manager.submits == []
    assert any("corrupt" in alert for alert in alerts)


def test_dry_run_shadow_survives_restart_without_gateway_calls(tmp_path: Path):
    class NoBrokerGateway(Gateway):
        def get_positions(self):
            raise AssertionError("dry-run must not call the broker")

    state_path = tmp_path / "tai.json"
    gateway = NoBrokerGateway()
    manager = Manager(gateway)
    settings = TaiDemoSettings("demo", "paper", False, True, dry_run=True)

    def make():
        return TaiDemoRunner(
            settings=settings,
            order_manager=manager,
            position_gateway=gateway,
            signal_provider=lambda _bar: TaiSignal(Direction.BUY, Decimal("100"), Decimal("99")),
            order_factory=lambda signal, cycle_id: OrderRequest(
                cycle_id,
                "NSXUSD",
                signal.direction,
                Decimal("3"),
                signal.entry,
                signal.stop,
                signal.levels()[2],
            ),
            control=Control(),
            state_path=state_path,
            epic="NSXUSD",
        )

    assert make().on_bar(bar()).action == "DRY_RUN"
    assert make().on_bar(bar()).reason == "one_position_rule"
    assert manager.submits == []
