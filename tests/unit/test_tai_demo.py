from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trader.execution.brokers.base import Direction, OrderReceipt, OrderRequest, Position
from trader.execution.control import ControlMode
from trader.execution.manager import ExecutionPaused
from trader.experiments.start_balance import StartBalanceStore
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
        self.raises = None
        self.submits = []

    def submit(self, cycle_id, order):
        self.submits.append((cycle_id, order))
        if self.raises is not None:
            raise self.raises
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


def runner(
    gateway,
    control=None,
    veto=False,
    journal=None,
    alerts=None,
    signal_provider=None,
    **kwargs,
):
    manager = Manager(gateway, veto=veto)
    signal = TaiSignal(Direction.BUY, Decimal("100"), Decimal("99"))
    return TaiDemoRunner(
        settings=TaiDemoSettings("demo", "paper", False, True),
        order_manager=manager,
        position_gateway=gateway,
        signal_provider=signal_provider or (lambda _bar: signal),
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


def _spread_rules(*, bid: str | None, ask: str | None, tick: str = "1") -> TaiInstrumentRules:
    return TaiInstrumentRules(
        Decimal("0.1"),
        Decimal("0.1"),
        Decimal("1"),
        Decimal(tick),
        Decimal("1"),
        Decimal("1"),
        bid=Decimal(bid) if bid is not None else None,
        ask=Decimal(ask) if ask is not None else None,
    )


def test_spread_guard_refuses_wide_quote_and_throttles_alert() -> None:
    gateway = Gateway()
    journal = []
    alerts = []
    tai, manager = runner(
        gateway,
        journal=journal.append,
        alerts=alerts,
        balance_provider=lambda: Decimal("1000"),
        instrument_rules_provider=lambda _epic: _spread_rules(bid="100", ask="103"),
    )
    first = tai.on_bar(bar())
    second = tai.on_bar(bar())
    assert first.action == second.action == "REJECTED"
    assert first.reason == second.reason == "spread_too_wide"
    assert manager.submits == []
    refusal = [event for event in journal if event.get("event") == "order_refused"]
    assert refusal[0]["spread"] == Decimal("3")
    assert refusal[0]["spread_limit"] == Decimal("2")
    assert len(alerts) == 1


def test_spread_guard_allows_exact_limit() -> None:
    gateway = Gateway()
    tai, manager = runner(
        gateway,
        balance_provider=lambda: Decimal("1000"),
        instrument_rules_provider=lambda _epic: _spread_rules(bid="100", ask="102"),
    )
    result = tai.on_bar(bar())
    assert result.action == "SUBMITTED"
    assert manager.submits


def test_spread_guard_refuses_missing_quote() -> None:
    gateway = Gateway()
    journal = []
    alerts = []
    tai, manager = runner(
        gateway,
        journal=journal.append,
        alerts=alerts,
        balance_provider=lambda: Decimal("1000"),
        instrument_rules_provider=lambda _epic: _spread_rules(bid=None, ask=None),
    )
    result = tai.on_bar(bar())
    assert result.action == "REJECTED"
    assert result.reason == "spread_quote_unavailable"
    assert manager.submits == []
    assert any(event.get("reason") == "spread_quote_unavailable" for event in journal)
    assert len(alerts) == 1


def test_spread_guard_records_dry_run_refusal() -> None:
    gateway = Gateway()
    journal = []
    tai, manager = runner(
        gateway,
        journal=journal.append,
        balance_provider=lambda: Decimal("1000"),
        instrument_rules_provider=lambda _epic: _spread_rules(bid="100", ask="103"),
        dry_run=True,
    )
    result = tai.on_bar(bar())
    assert result.action == "REJECTED"
    assert result.reason == "spread_too_wide"
    assert manager.submits == []
    assert any(event.get("event") == "order_refused" for event in journal)


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


def test_paused_order_manager_reason_reaches_alert_and_journal():
    events = []
    alerts = []
    tai, manager = runner(Gateway(), journal=events.append, alerts=alerts)
    manager.raises = ExecutionPaused("execution lease is unavailable")
    result = tai.on_bar(bar())
    assert result.reason == "guardian_veto"
    assert any(
        event["event"] == "guardian_veto" and event["reason"] == "execution lease is unavailable"
        for event in events
    )
    assert any("execution lease is unavailable" in alert for alert in alerts)


def test_outcome_telemetry_counts_reasons_entries_and_exits():
    gateway = Gateway()
    no_signal, _ = runner(gateway, signal_provider=lambda _bar: None)
    no_signal.on_bar(bar(closed=False))
    no_signal.on_bar(bar(market_open=False))
    no_signal.on_bar(bar())
    no_signal.on_bar(bar(in_break=True))
    counts = no_signal.health_snapshot()["NSXUSD"]
    assert counts["evaluated"] == 4
    assert counts["stale_or_incomplete"] == 1
    assert counts["market_closed_or_break"] == 2
    assert counts["no_signal"] == 1
    assert counts["refused"] == {}

    refused, _ = runner(
        Gateway(),
        instrument_rules_provider=lambda _epic: _spread_rules(bid="100", ask="103"),
    )
    assert refused.on_bar(bar()).reason == "spread_too_wide"
    assert refused.health_snapshot()["NSXUSD"]["refused"] == {"spread_too_wide": 1}

    live_gateway = Gateway()
    live, _ = runner(live_gateway)
    assert live.on_bar(bar()).action == "SUBMITTED"
    assert live.on_bar(bar(high="103")).action == "SUBMITTED"
    live_counts = live.health_snapshot()["NSXUSD"]
    assert live_counts["entry"] == 2
    assert live_counts["exit"] == 1


def test_each_outcome_has_deterministic_bar_audit() -> None:
    events = []
    tai, _ = runner(
        Gateway(),
        journal=events.append,
        signal_provider=lambda _bar: None,
    )

    result = tai.on_bar(bar())

    decisions = [event for event in events if event.get("event") == "decision"]
    assert len(decisions) == 1
    assert decisions[0]["action"] == result.action == "NO_TRADE"
    assert decisions[0]["reason"] == result.reason == "no_signal"
    assert decisions[0]["bar_fingerprint"]
    assert decisions[0]["at"] == bar().at.isoformat()
    assert decisions[0]["epic"] == "NSXUSD"


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


def test_lot_below_broker_minimum_is_refused():
    rules = TaiInstrumentRules(Decimal("0.2"), Decimal("0.1"))
    with pytest.raises(TaiDemoRefused, match="below the broker minimum"):
        _sizing_runner("1000")._requested_size(rules)


def test_missing_balance_provider_is_refused_when_rules_are_available():
    instance, _ = runner(Gateway())
    with pytest.raises(TaiDemoRefused, match="balance provider is required"):
        instance._requested_size(TaiInstrumentRules(Decimal("0.1"), Decimal("0.1")))


def test_unset_start_balance_means_automatic_not_a_startup_refusal():
    class Cfg:
        broker_env = "demo"
        trading_mode = "paper"
        live_trading_enabled = False
        tai_demo_experiment = True
        tai_demo_base_lot = "0.1"
        tai_demo_start_balance = "0"

    assert TaiDemoSettings.from_settings(Cfg()).start_balance == Decimal("0")


def _automatic_runner(balance, store, alerts=None, journal=None):
    gateway = Gateway()
    return TaiDemoRunner(
        settings=TaiDemoSettings("demo", "paper", False, True, start_balance=Decimal("0")),
        order_manager=Manager(gateway),
        position_gateway=gateway,
        signal_provider=lambda _bar: None,
        order_factory=lambda *_: None,
        control=Control(),
        balance_provider=lambda: Decimal(balance),
        alert=(alerts.append if alerts is not None else None),
        journal=journal,
        start_balance_store=store,
    )


RULES = TaiInstrumentRules(Decimal("0.01"), Decimal("0.01"))


def test_automatic_start_balance_is_recorded_on_first_sizing_and_trades_the_base_lot(tmp_path):
    store = StartBalanceStore(tmp_path / "start.json")
    alerts, events = [], []
    instance = _automatic_runner("20987.63", store, alerts, events.append)
    assert instance._requested_size(RULES) == Decimal("0.10")
    assert store.get() == Decimal("20987.63")
    assert len(alerts) == 1 and "20987.63" in alerts[0]
    assert [e["event"] for e in events] == ["start_balance_recorded"]
    instance._requested_size(RULES)
    assert len(alerts) == 1  # told once, not on every trade


def test_automatic_start_balance_scales_lots_with_later_growth(tmp_path):
    store = StartBalanceStore(tmp_path / "start.json")
    _automatic_runner("1000", store)._requested_size(RULES)
    assert _automatic_runner("1500", store)._requested_size(RULES) == Decimal("0.15")
    assert _automatic_runner("500", store)._requested_size(RULES) == Decimal("0.10")


def test_automatic_start_balance_survives_a_restart(tmp_path):
    path = tmp_path / "start.json"
    _automatic_runner("1000", StartBalanceStore(path))._requested_size(RULES)
    reopened = _automatic_runner("2000", StartBalanceStore(path))
    assert reopened._requested_size(RULES) == Decimal("0.20")


def test_an_explicit_start_balance_overrides_the_saved_one(tmp_path):
    store = StartBalanceStore(tmp_path / "start.json")
    store.record_if_missing(Decimal("1000"))
    assert _sizing_runner("1500", "1500")._requested_size(RULES) == Decimal("0.10")
    assert store.get() == Decimal("1000")


def test_no_start_balance_and_no_store_refuses_the_entry():
    with pytest.raises(TaiDemoRefused, match="no start balance"):
        _automatic_runner("1000", None)._requested_size(RULES)


def test_a_damaged_start_balance_file_refuses_instead_of_guessing(tmp_path):
    path = tmp_path / "start.json"
    path.write_text("garbage", encoding="utf-8")
    with pytest.raises(TaiDemoRefused, match="start balance unavailable"):
        _automatic_runner("1000", StartBalanceStore(path))._requested_size(RULES)
    assert path.read_text(encoding="utf-8") == "garbage"


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


def test_dry_run_structure_stop_frees_next_entry_and_alerts_flat():
    signal = TaiSignal(Direction.BUY, Decimal("100"), Decimal("99"))
    signals = iter((signal, None, signal))
    alerts = []
    instance, manager = runner(
        Gateway(),
        signal_provider=lambda _bar: next(signals),
        alerts=alerts,
        dry_run=True,
    )
    assert instance.on_bar(bar()).action == "DRY_RUN"
    assert instance.on_bar(bar(low="98")).reason == "no_signal"
    assert instance.on_bar(bar()).action == "DRY_RUN"
    assert manager.submits == []
    assert any("flat" in alert and "stop" in alert for alert in alerts)


def test_dry_run_entry_stop_after_target_one_frees_next_entry_and_alerts_flat():
    signal = TaiSignal(Direction.BUY, Decimal("100"), Decimal("99"))
    signals = iter((signal, None, signal))
    alerts = []
    instance, _ = runner(
        Gateway(),
        signal_provider=lambda _bar: next(signals),
        alerts=alerts,
        dry_run=True,
    )
    assert instance.on_bar(bar()).action == "DRY_RUN"
    assert instance.on_bar(bar(price="100.5", high="101", low="100.5")).reason == (
        "one_position_rule"
    )
    assert instance.on_bar(bar(price="99.5", high="100", low="99.5")).reason == "no_signal"
    assert instance.on_bar(bar()).action == "DRY_RUN"
    assert any("flat" in alert and "stop_entry" in alert for alert in alerts)


def test_live_missing_position_is_dropped_and_later_signal_can_enter():
    signal = TaiSignal(Direction.BUY, Decimal("100"), Decimal("99"))
    signals = iter((signal, None, signal))
    gateway = Gateway()
    events = []
    alerts = []
    instance, manager = runner(
        gateway,
        signal_provider=lambda _bar: next(signals),
        journal=events.append,
        alerts=alerts,
    )
    assert instance.on_bar(bar()).action == "SUBMITTED"
    gateway.positions.clear()
    assert instance.on_bar(bar()).reason == "no_signal"
    assert instance.on_bar(bar()).action == "SUBMITTED"
    assert len(manager.submits) == 2
    assert any(event["event"] == "reconcile_closed" for event in events)
    assert any("CLOSED" in alert and "Capital.com" in alert for alert in alerts)


def test_broker_refusing_a_partial_exit_does_not_crash_the_bar_and_alerts_once():
    class RefusingGateway(Gateway):
        def close_partial(self, deal_id, size):
            raise RuntimeError("broker refused")

    gateway = RefusingGateway()
    alerts: list[str] = []
    journal: list[dict] = []
    instance, _ = runner(gateway, alerts=alerts, journal=journal.append)
    instance.on_bar(bar())
    instance.on_bar(bar(high="101"))  # target 1: refused partial, bar must still complete
    instance.on_bar(bar(high="102"))
    assert gateway.partial == []
    assert [e["event"] for e in journal].count("scale_out_failed") == 2
    assert sum("scale_out_failed" in message for message in alerts) == 1


def test_stop_to_entry_is_retried_on_later_bars_after_a_broker_refusal():
    class FlakyGateway(Gateway):
        failures = 1

        def modify_position(self, deal_id, *, stop_level, profit_level=None):
            if self.failures:
                self.failures -= 1
                raise ValueError("too close to market")
            return super().modify_position(
                deal_id, stop_level=stop_level, profit_level=profit_level
            )

    gateway = FlakyGateway()
    instance, _ = runner(gateway)
    instance.on_bar(bar())
    instance.on_bar(bar(high="101"))  # target 1 hit, break-even refused
    assert gateway.modified == []
    instance.on_bar(bar())  # next bar retries without needing the target again
    assert gateway.modified == [("deal-1", Decimal("100"), Decimal("103"))]


def test_break_even_stop_never_sits_on_the_wrong_side_of_the_fill():
    gateway = Gateway()
    instance, manager = runner(gateway)
    instance.on_bar(bar())
    # the fill came in better than the signal entry (BUY filled below 100)
    gateway.positions[0] = Position(
        "deal-1",
        "NSXUSD",
        Direction.BUY,
        Decimal("3"),
        Decimal("99.5"),
        Decimal("99"),
        Decimal("103"),
    )
    instance.on_bar(bar(high="101"))
    assert gateway.modified[0][1] == Decimal("99.5")


class _ExternalSignals:
    """Signal source that only knows how to build an entry for an outside decision."""

    def __call__(self, _bar):
        return TaiSignal(Direction.BUY, Decimal("100"), Decimal("99"))

    def external_signal(self, direction, rules):
        entry = rules.ask if direction is Direction.BUY else rules.bid
        stop = entry - Decimal("10") if direction is Direction.BUY else entry + Decimal("10")
        return TaiSignal(direction, entry, stop)


def _external_runner(gateway, **kwargs):
    return runner(
        gateway,
        signal_provider=_ExternalSignals(),
        balance_provider=lambda: Decimal("1000"),
        instrument_rules_provider=lambda _epic: _spread_rules(bid="100", ask="100.5"),
        epic="NSXUSD",
        **kwargs,
    )


def test_external_signal_enters_through_the_order_manager() -> None:
    gateway = Gateway()
    tai, manager = _external_runner(gateway)
    outcome = tai.on_external_signal(Direction.BUY)
    assert (outcome.action, outcome.reason) == ("SUBMITTED", "entry_submitted")
    cycle_id, order = manager.submits[0]
    assert cycle_id.startswith("tai-tv-")
    assert order.direction is Direction.BUY and order.reference_price == Decimal("100.5")
    assert order.stop_level == Decimal("90.5")


def test_external_signal_replaces_the_open_trade_and_respects_kill_and_pause() -> None:
    gateway = Gateway()
    tai, manager = _external_runner(gateway)
    assert tai.on_external_signal(Direction.BUY).action == "SUBMITTED"
    again = tai.on_external_signal(Direction.SELL)
    assert (again.action, again.reason) == ("SUBMITTED", "entry_submitted")
    assert gateway.closed == ["deal-1"] and len(manager.submits) == 2
    assert manager.submits[1][1].direction is Direction.SELL

    paused, paused_manager = _external_runner(Gateway(), control=Control(ControlMode.PAUSE))
    assert paused.on_external_signal(Direction.BUY).reason == "execution_paused"
    assert paused_manager.submits == []

    killed_gateway = Gateway()
    killed, killed_manager = _external_runner(killed_gateway, control=Control(ControlMode.KILL))
    assert killed.on_external_signal(Direction.BUY).reason == "kill_switch_active"
    assert killed_manager.submits == []


def test_external_signal_ignores_a_wrong_market_closed_flag_and_the_spread_guard() -> None:
    gateway = Gateway()
    tai, manager = _external_runner(gateway)
    tai.on_bar(bar(market_open=False, in_break=True))
    outcome = tai.on_external_signal(Direction.SELL)
    assert (outcome.action, outcome.reason) == ("SUBMITTED", "entry_submitted")
    assert len(manager.submits) == 1


def test_external_signal_uses_the_relaxed_guardian_path_when_available() -> None:
    gateway = Gateway()
    tai, manager = _external_runner(gateway)
    relaxed = []
    manager.submit_relaxed = lambda cycle_id, order: (
        relaxed.append(order) or manager.submit(cycle_id, order)
    )
    assert tai.on_external_signal(Direction.BUY).action == "SUBMITTED"
    assert len(relaxed) == 1


def test_tradingview_mode_never_opens_an_entry_from_the_local_signal() -> None:
    gateway = Gateway()
    tai, manager = _external_runner(gateway)
    tai.external_signals_only = True
    outcome = tai.on_bar(bar())
    assert (outcome.action, outcome.reason) == ("NO_TRADE", "awaiting_tradingview_alert")
    assert manager.submits == []


def test_external_signal_that_cannot_be_built_is_refused_not_traded() -> None:
    gateway = Gateway()
    alerts = []

    class Broken(_ExternalSignals):
        def external_signal(self, direction, rules):
            raise ValueError("ATR is not ready for an external signal")

    tai, manager = runner(
        gateway,
        signal_provider=Broken(),
        alerts=alerts,
        balance_provider=lambda: Decimal("1000"),
        instrument_rules_provider=lambda _epic: _spread_rules(bid="100", ask="100.5"),
        epic="NSXUSD",
    )
    outcome = tai.on_external_signal(Direction.BUY)
    assert (outcome.action, outcome.reason) == ("REJECTED", "external_signal_unusable")
    assert manager.submits == [] and alerts


def test_entry_alert_text_carries_the_full_plan() -> None:
    from decimal import Decimal

    from trader.experiments.tai_demo import Direction, TaiDemoRunner, TaiSignal

    signal = TaiSignal(
        direction=Direction.SELL,
        entry=Decimal("26540.0"),
        stop=Decimal("26542.6"),
        targets=(Decimal("26537.4"), Decimal("26534.8"), Decimal("26532.2")),
    )
    text = TaiDemoRunner._entry_text(
        object.__new__(TaiDemoRunner),
        "US100",
        signal,
        Decimal("0.11"),
        Decimal("26540.1"),
        Decimal("2.86"),
    )
    for part in (
        "ENTRY SELL US100",
        "Size: 0.11",
        "Entry: 26540.1",
        "SL: 26542.6",
        "1R = $2.86",
        "TP1: 26537.4",
        "TP2: 26534.8",
        "TP3: 26532.2",
    ):
        assert part in text


def test_check_broker_closed_notices_a_closed_trade_without_a_bar():
    signal = TaiSignal(Direction.BUY, Decimal("100"), Decimal("99"))
    gateway = Gateway()
    events = []
    alerts = []
    instance, _manager = runner(
        gateway,
        signal_provider=lambda _bar: signal,
        journal=events.append,
        alerts=alerts,
    )
    assert instance.on_bar(bar()).action == "SUBMITTED"
    instance.check_broker_closed()  # still open: nothing happens
    assert not any(event["event"] == "reconcile_closed" for event in events)
    gateway.positions.clear()
    instance.check_broker_closed()
    assert any(event["event"] == "reconcile_closed" for event in events)
    assert any("CLOSED" in alert for alert in alerts)
    instance.check_broker_closed()  # nothing tracked any more: quiet
    assert sum(event["event"] == "reconcile_closed" for event in events) == 1


def test_external_signal_passes_the_tradingview_reference_to_the_builder() -> None:
    seen = []

    class Builder(_ExternalSignals):
        def external_signal(self, direction, rules, reference=None):
            seen.append(reference)
            return super().external_signal(direction, rules)

    gateway = Gateway()
    instance, _manager = runner(
        gateway,
        signal_provider=Builder(),
        balance_provider=lambda: Decimal("1000"),
        instrument_rules_provider=lambda _epic: _spread_rules(bid="100", ask="100.5"),
        epic="NSXUSD",
    )
    instance.on_external_signal(Direction.BUY, reference_price=Decimal("101"))
    assert seen == [Decimal("101")]
