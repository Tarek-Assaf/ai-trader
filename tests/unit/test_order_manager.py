"""Offline execution failure and recovery checks."""

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from trader.core.db import tables
from trader.execution.brokers.base import Direction, OrderRequest
from trader.execution.brokers.paper import PaperBroker, PaperExecutionCosts
from trader.execution.control import (
    CompositeControlSource,
    ControlMode,
    FlagFileControlSource,
    RedisControlSource,
)
from trader.execution.journal import ExecutionJournal
from trader.execution.manager import BoundApproval, ExecutionPaused, OrderManager
from trader.risk.guardian import RiskDecision


class Lease:
    fail = False

    def acquire(self):
        self.check()

    def check(self):
        if self.fail:
            raise RuntimeError("lost")


class Broker(PaperBroker):
    calls = 0
    timeout = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.working_orders = []
        self.fail_close = False

    def place_order(self, order):
        self.calls += 1
        receipt = super().place_order(order)
        if self.timeout:
            raise TimeoutError("accepted but reply lost")
        return receipt

    def get_open_orders(self):
        return tuple(self.working_orders)

    def cancel_working_order(self, deal_id):
        self.working_orders = [
            order
            for order in self.working_orders
            if order.get("workingOrderData", {}).get("dealId") != deal_id
        ]
        if any(
            order.get("workingOrderData", {}).get("dealId") == deal_id
            for order in self.working_orders
        ):
            raise ValueError("working order could not be canceled")
        return f"paper-cancel-{deal_id}"

    def close_position(self, deal_id):
        if self.fail_close:
            self.fail_close = False
            raise OSError("temporary close failure")
        return super().close_position(deal_id)


def setup(
    tmp_path, *, pnl=None, approve=None, control_source=None, control_writer=None, manage=None
):
    engine = create_engine(f"sqlite:///{tmp_path / 'execution.db'}")
    tables["trade_intents"].create(engine)
    tables["snapshots"].create(engine)
    journal = ExecutionJournal(engine)
    broker = Broker(
        starting_cash=Decimal("1000"),
        currency="USD",
        costs=PaperExecutionCosts(
            spread_points={"US100": Decimal("2")},
            slippage_points={"US100": Decimal("0.5")},
        ),
    )
    alerts = []
    lease = Lease()

    def approval(order, cycle):
        return BoundApproval(order, cycle, RiskDecision(True, order.size, (), ()))

    if control_writer is None:
        control_writer = RedisControlSource(Redis())
    if control_source is None:
        control_source = CompositeControlSource(
            (FlagFileControlSource(tmp_path / "control"), control_writer)
        )

    manager = OrderManager(
        broker,
        journal,
        lease,
        Lease(),
        approve or approval,
        pnl or (lambda positions: None),
        alerts.append,
        control_source,
        control_writer,
        manage or (lambda positions: None),
    )
    manager.startup()
    order = OrderRequest(
        "cycle-1", "US100", Direction.BUY, Decimal("1"), Decimal("100"), Decimal("90")
    )
    return manager, broker, journal, lease, alerts, order


class Redis:
    def __init__(self):
        self.values = {}
        self.fail = False

    def get(self, key):
        if self.fail:
            raise OSError("unavailable")
        return self.values.get(key)

    def set(self, key, value):
        if self.fail:
            raise OSError("unavailable")
        self.values[key] = value

    def delete(self, key):
        if self.fail:
            raise OSError("unavailable")
        self.values.pop(key, None)


def test_duplicate_trigger_never_submits_twice(tmp_path):
    manager, broker, journal, _, _, order = setup(tmp_path)
    assert manager.submit("cycle-1", order)
    assert manager.submit("cycle-1", order) is None
    assert broker.calls == 1
    assert journal.get("cycle-1")["initial_stop"] == "90"


def test_concurrent_duplicate_triggers_submit_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    manager, broker, _, _, _, order = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: manager.submit("cycle-1", order), range(20)))
    assert sum(result is not None for result in results) == 1
    assert broker.calls == 1


def test_runtime_order_calls_only_exist_in_manager():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "trader"
    callers = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "place_order":
                    callers.append(path.relative_to(root).as_posix())
    assert callers == ["execution/manager.py"]


def test_redis_loss_before_order_fails_closed(tmp_path):
    manager, broker, _, lease, alerts, order = setup(tmp_path)
    lease.fail = True
    with pytest.raises(ExecutionPaused):
        manager.submit("cycle-1", order)
    assert broker.calls == 0 and manager.paused and alerts


def test_timeout_after_acceptance_polls_and_does_not_retry(tmp_path):
    manager, broker, journal, _, alerts, order = setup(tmp_path)
    broker.timeout = True
    with pytest.raises(ExecutionPaused):
        manager.submit("cycle-1", order)
    assert len(manager.positions) == 1
    assert journal.get("cycle-1")["execution_state"] == "ambiguous"
    with pytest.raises(ExecutionPaused):
        manager.submit("cycle-1", order)
    assert broker.calls == 1 and alerts


def test_approval_for_changed_order_is_rejected(tmp_path):
    def wrong(order, cycle):
        return BoundApproval(
            replace(order, size=Decimal("2")), cycle, RiskDecision(True, order.size, (), ())
        )

    manager, broker, _, _, _, order = setup(tmp_path, approve=wrong)
    with pytest.raises(ExecutionPaused):
        manager.submit("cycle-1", order)
    assert broker.calls == 0


def test_pnl_exception_pauses_and_alerts(tmp_path):
    manager, broker, _, _, alerts, order = setup(tmp_path)

    def failure(positions):
        raise ValueError("non-positive equity")

    manager._check_pnl = failure
    with pytest.raises(ExecutionPaused):
        manager.submit("cycle-1", order)
    assert manager.paused and alerts and broker.calls == 0


def test_pause_is_durable_and_requires_explicit_recovery(tmp_path):
    manager, broker, journal, lease, alerts, order = setup(tmp_path)
    lease.fail = True
    with pytest.raises(ExecutionPaused):
        manager.submit("cycle-1", order)
    lease.fail = False
    assert journal.is_paused()
    manager.startup()
    assert manager.paused
    manager.resume()
    assert not manager.paused and not journal.is_paused()


def test_process_killed_after_reservation_never_resubmits(tmp_path):
    import subprocess
    import sys

    manager, broker, journal, lease, alerts, order = setup(tmp_path)
    script = tmp_path / "reserve.txt"
    database_url = "sqlite:///" + str(tmp_path / "execution.db")
    script.write_text(
        "from sqlalchemy import create_engine\n"
        "from trader.execution.journal import ExecutionJournal\n"
        "from trader.execution.brokers.base import OrderRequest, Direction\n"
        "from decimal import Decimal\n"
        "import time\n"
        f"j = ExecutionJournal(create_engine({database_url!r}))\n"
        "j.reserve('cycle-1', OrderRequest('cycle-1','US100',Direction.BUY,"
        "Decimal('1'),Decimal('100'),Decimal('90')))\n"
        "print('reserved', flush=True)\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "reserved"
        process.terminate()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    assert not journal.reserve("cycle-1", order)
    manager.startup()
    with pytest.raises(ExecutionPaused):
        manager.submit("cycle-1", order)
    assert broker.calls == 0 and manager.paused and alerts
    manager.resolve_intent("cycle-1", "rejected", reason="operator verified empty broker state")
    manager.resume()
    assert manager.submit("cycle-1", order) is None
    assert broker.calls == 0


def test_accepted_timeout_can_be_matched_and_resumed_without_resubmission(tmp_path):
    manager, broker, journal, _, _, order = setup(tmp_path)
    broker.timeout = True
    with pytest.raises(ExecutionPaused):
        manager.submit("cycle-1", order)
    deal = broker.get_positions()[0].deal_id
    with pytest.raises(ExecutionPaused):
        manager.resolve_intent("cycle-1", "rejected", reason="not actually rejected")
    manager.resolve_intent("cycle-1", "matched", deal_id=deal, reason="broker position verified")
    assert journal.get("cycle-1")["operator_resolutions"]
    manager.resume()
    assert manager.submit("cycle-1", order) is None
    assert broker.calls == 1


def test_normal_exit_and_partial_close_allow_next_cycle(tmp_path):
    manager, broker, _, _, _, order = setup(tmp_path)
    receipt = manager.submit("cycle-1", order)
    broker.close_partial(receipt.deal_id, Decimal("0.5"))
    assert manager.reconcile() and not manager.paused
    # A real quote update triggers PaperBroker's server-side bracket handling.
    closed = broker.update_quote("US100", bid=Decimal("85"), ask=Decimal("87"))
    assert receipt.deal_id in closed
    assert manager.reconcile() and not manager.paused
    broker.update_quote("US100", bid=Decimal("99"), ask=Decimal("101"))
    assert manager.submit("cycle-2", replace(order, client_order_id="cycle-2"))


@pytest.mark.parametrize("mismatch", ["larger", "unknown"])
def test_larger_or_unknown_broker_position_pauses(tmp_path, mismatch):
    manager, broker, _, _, alerts, order = setup(tmp_path)
    receipt = manager.submit("cycle-1", order)
    if mismatch == "larger":
        broker._positions[receipt.deal_id] = replace(receipt.position, size=Decimal("2"))
    else:
        broker.place_order(replace(order, client_order_id="external"))
    assert not manager.reconcile()
    assert manager.paused and alerts


def test_tick_renews_both_leases_and_lost_guardian_pauses(tmp_path):
    manager, _, _, _, alerts, _ = setup(tmp_path)

    class ExpiringLease:
        checks = 0
        fail = False

        def check(self):
            self.checks += 1
            if self.fail:
                raise RuntimeError("expired")

    guardian = ExpiringLease()
    manager._guardian_lease = guardian
    manager._last_reconcile = None
    manager.tick()
    assert guardian.checks == 1
    guardian.fail = True
    manager._last_reconcile = None
    with pytest.raises(ExecutionPaused):
        manager.tick()
    assert manager.paused and alerts


def test_pause_keeps_managing_existing_positions_and_rejects_new_entries(tmp_path):
    redis = Redis()
    writer = RedisControlSource(redis)
    source = CompositeControlSource((FlagFileControlSource(tmp_path / "control"), writer))
    managed = []
    manager, broker, _, _, _, order = setup(
        tmp_path, control_source=source, control_writer=writer, manage=managed.append
    )
    manager.submit("cycle-1", order)
    position = broker.get_positions()[0]
    writer.set(ControlMode.PAUSE)
    manager.tick()
    assert broker.get_positions() == (position,)
    assert managed[-1] == (position,)
    with pytest.raises(ExecutionPaused):
        manager.submit("cycle-2", replace(order, client_order_id="cycle-2"))
    writer.set(ControlMode.RESUME)
    manager.tick()
    assert not manager.paused


def test_startup_pause_reconciles_positions_before_management(tmp_path):
    redis = Redis()
    writer = RedisControlSource(redis)
    source = CompositeControlSource((FlagFileControlSource(tmp_path / "control"), writer))
    manager, broker, journal, _, alerts, order = setup(
        tmp_path, control_source=source, control_writer=writer
    )
    manager.submit("cycle-1", order)
    position = broker.get_positions()[0]
    writer.set(ControlMode.PAUSE)
    managed = []

    def approval(request, cycle):
        return BoundApproval(request, cycle, RiskDecision(True, request.size, (), ()))

    restarted = OrderManager(
        broker,
        journal,
        Lease(),
        Lease(),
        approval,
        lambda positions: None,
        alerts.append,
        source,
        writer,
        managed.append,
    )
    restarted.startup()
    assert restarted.paused
    assert restarted.positions == (position,)
    restarted.tick()
    assert managed == [(position,)]


def test_kill_flattens_and_survives_restart_until_explicit_resume(tmp_path):
    redis = Redis()
    writer = RedisControlSource(redis)
    source = CompositeControlSource((FlagFileControlSource(tmp_path / "control"), writer))
    manager, broker, journal, _, _, order = setup(
        tmp_path, control_source=source, control_writer=writer
    )
    manager.submit("cycle-1", order)
    writer.set(ControlMode.KILL)
    manager.tick()
    assert broker.get_positions() == ()
    assert manager.paused and journal.is_killed()

    def approval(request, cycle):
        return BoundApproval(request, cycle, RiskDecision(True, request.size, (), ()))

    restarted = OrderManager(
        broker,
        journal,
        Lease(),
        Lease(),
        approval,
        lambda positions: None,
        lambda reason: None,
        source,
        writer,
        lambda positions: None,
    )
    restarted.startup()
    next_order = replace(order, client_order_id="cycle-2")
    assert restarted.paused
    with pytest.raises(ExecutionPaused):
        restarted.submit("cycle-2", replace(next_order, client_order_id="cycle-2"))
    writer.set(ControlMode.RESUME)
    restarted.tick()
    assert not restarted.paused and not journal.is_killed()
    assert restarted.submit("cycle-2", replace(next_order, client_order_id="cycle-2"))


def test_kill_during_order_closes_new_fill_before_returning(tmp_path):
    redis = Redis()
    writer = RedisControlSource(redis)
    source = CompositeControlSource((FlagFileControlSource(tmp_path / "control"), writer))
    manager, broker, journal, _, _, order = setup(
        tmp_path, control_source=source, control_writer=writer
    )
    original_place = broker.place_order

    def kill_after_accept(request):
        receipt = original_place(request)
        writer.set(ControlMode.KILL)
        return receipt

    broker.place_order = kill_after_accept
    with pytest.raises(ExecutionPaused, match="kill activated during order submission"):
        manager.submit("cycle-1", order)
    assert broker.calls == 1
    assert broker.get_positions() == ()
    assert journal.is_killed()


def test_kill_is_checked_after_approval_and_before_order_call(tmp_path):
    redis = Redis()
    writer = RedisControlSource(redis)
    source = CompositeControlSource((FlagFileControlSource(tmp_path / "control"), writer))

    def approval(request, cycle):
        writer.set(ControlMode.KILL)
        return BoundApproval(request, cycle, RiskDecision(True, request.size, (), ()))

    manager, broker, journal, _, _, order = setup(
        tmp_path, approve=approval, control_source=source, control_writer=writer
    )
    with pytest.raises(ExecutionPaused, match="control activated before broker submission"):
        manager.submit("cycle-1", order)
    assert broker.calls == 0 and journal.is_killed()
    assert journal.get("cycle-1")["execution_state"] == "rejected"


def test_kill_cancels_working_orders_and_retries_failed_flatten(tmp_path):
    redis = Redis()
    writer = RedisControlSource(redis)
    source = CompositeControlSource((FlagFileControlSource(tmp_path / "control"), writer))
    manager, broker, journal, _, alerts, order = setup(
        tmp_path, control_source=source, control_writer=writer
    )
    manager.submit("cycle-1", order)
    broker.working_orders.append({"workingOrderData": {"dealId": "working-1"}})
    broker.fail_close = True
    writer.set(ControlMode.KILL)
    manager.tick()
    assert broker.get_positions() and broker.get_open_orders() == ()
    assert manager.paused and journal.is_killed() and alerts
    manager.tick()
    assert broker.get_positions() == () and broker.get_open_orders() == ()


def test_volume_flag_is_independent_and_kill_precedes_pause(tmp_path):
    flags = tmp_path / "control"
    flags.mkdir()
    redis = Redis()
    writer = RedisControlSource(redis)
    source = CompositeControlSource((FlagFileControlSource(flags), writer))
    (flags / "pause.flag").touch()
    assert source.read() is ControlMode.PAUSE
    (flags / "kill.flag").touch()
    assert source.read() is ControlMode.KILL


def test_control_source_failure_fails_closed(tmp_path):
    redis = Redis()
    redis.fail = True
    writer = RedisControlSource(redis)
    source = CompositeControlSource((FlagFileControlSource(tmp_path / "control"), writer))
    manager, broker, _, _, alerts, order = setup(tmp_path, control_source=source)
    with pytest.raises(ExecutionPaused):
        manager.submit("cycle-1", order)
    assert broker.calls == 0 and manager.paused and alerts


def test_resume_resets_external_alert_latches_for_second_pause(tmp_path):
    redis = Redis()
    writer = RedisControlSource(redis)
    source = CompositeControlSource((FlagFileControlSource(tmp_path / "control"), writer))
    manager, _, _, _, alerts, _ = setup(tmp_path, control_source=source, control_writer=writer)
    writer.set(ControlMode.PAUSE)
    manager.tick()
    assert alerts.count("external_pause") == 1
    writer.set(ControlMode.RESUME)
    manager.tick()
    assert not manager.paused
    writer.set(ControlMode.PAUSE)
    manager.tick()
    assert alerts.count("external_pause") == 2


class PreviousOwnerLease(Lease):
    """A lease still held by the previous process for a few more attempts."""

    def __init__(self, failures: int) -> None:
        self.failures = failures

    def acquire(self):
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("held by the previous process")


def test_startup_waits_for_a_lease_the_previous_process_still_holds(tmp_path):
    manager, _, _, _, alerts, _ = setup(tmp_path)
    manager._started = False
    manager._paused = True
    manager._execution_lease = PreviousOwnerLease(1)
    manager._startup_lease_wait = 5.0
    manager.startup()
    assert manager._started and not manager.paused
    assert "execution_startup_failed" not in alerts


def test_startup_without_wait_pauses_when_the_lease_is_held(tmp_path):
    manager, _, _, _, alerts, _ = setup(tmp_path)
    manager._started = False
    manager._execution_lease = PreviousOwnerLease(1)
    with pytest.raises(ExecutionPaused):
        manager.startup()
    assert manager.paused and "execution_startup_failed" in alerts


def test_shutdown_releases_both_leases_and_refuses_entries(tmp_path):
    manager, _, _, _, _, order = setup(tmp_path)
    released = []

    class ReleasingLease(Lease):
        def release(self):
            released.append(True)

    manager._execution_lease = ReleasingLease()
    manager._guardian_lease = ReleasingLease()
    manager.shutdown()
    assert len(released) == 2 and manager.paused
    with pytest.raises(ExecutionPaused, match="not started"):
        manager.submit("cycle-after-shutdown", order)


def test_persistent_pause_reason_alerts_once_until_resume(tmp_path):
    manager, _, _, _, alerts, _ = setup(tmp_path)
    manager._pause("execution_lease_lost")
    manager._pause("execution_lease_lost")
    assert alerts.count("execution_lease_lost") == 1
    manager._pause("broker_state_mismatch")
    assert alerts.count("broker_state_mismatch") == 1


def _tai_runner(manager, broker, alerts):
    from trader.experiments.tai_demo import (
        TaiDemoRunner,
        TaiDemoSettings,
        TaiSignal,
    )

    signal = TaiSignal(Direction.BUY, Decimal("100"), Decimal("90"))
    return TaiDemoRunner(
        settings=TaiDemoSettings("demo", "paper", False, True),
        order_manager=manager,
        position_gateway=broker,
        signal_provider=lambda _bar: signal,
        order_factory=lambda sig, cycle_id: OrderRequest(
            cycle_id,
            "US100",
            sig.direction,
            Decimal("1"),
            sig.entry,
            sig.stop,
            sig.levels()[2],
        ),
        control=type("C", (), {"read": lambda self: ControlMode.RUN})(),
        alert=alerts.append,
    )


def _tai_bar():
    from datetime import UTC, datetime

    from trader.experiments.tai_demo import ClosedBar

    return ClosedBar(
        datetime(2026, 10, 19, 8, 0, tzinfo=UTC),
        "US100",
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        closed=True,
        market_open=True,
        in_break=False,
    )


def test_tai_signal_reaches_the_broker_through_the_real_order_manager(tmp_path):
    manager, broker, _journal, _lease, alerts, _order = setup(tmp_path)
    runner = _tai_runner(manager, broker, alerts)
    outcome = runner.on_bar(_tai_bar())
    assert outcome.action == "SUBMITTED"
    assert broker.calls == 1
    assert len(broker.get_positions()) == 1
    manager.tick()
    assert not manager.paused
    assert runner.on_bar(_tai_bar()).reason == "one_position_rule"
    assert broker.calls == 1


def test_tai_signal_is_refused_when_the_manager_is_not_started(tmp_path):
    manager, broker, _journal, _lease, alerts, _order = setup(tmp_path)
    manager.shutdown()
    outcome = _tai_runner(manager, broker, alerts).on_bar(_tai_bar())
    assert outcome.action != "SUBMITTED"
    assert broker.calls == 0


def test_unresolved_order_pauses_as_mismatch_even_when_the_broker_limits_activity_to_a_day(
    tmp_path,
):
    from datetime import timedelta

    manager, broker, journal, _lease, alerts, order = setup(tmp_path)

    def strict_activity(start, end):
        if end - start > timedelta(days=1):
            raise ValueError("activity interval must be positive and at most one day")
        return ()

    broker.get_activity = strict_activity
    journal.reserve("cycle-1", order)
    journal.mark("cycle-1", "ambiguous")

    assert manager.reconcile() is False
    assert manager.paused
    assert alerts[-1] == "broker_state_mismatch"


def test_a_broker_refusal_is_recorded_without_pausing_the_bot(tmp_path):
    from trader.execution.brokers.base import OrderRejected

    manager, broker, journal, _lease, alerts, order = setup(tmp_path)

    def refuse(_order):
        raise OrderRejected("error.invalid.stoploss.minvalue")

    broker.place_order = refuse

    with pytest.raises(ExecutionPaused, match="error.invalid.stoploss.minvalue"):
        manager.submit("cycle-1", order)

    assert journal.get("cycle-1")["execution_state"] == "rejected"
    assert not manager.paused
    assert alerts[-1] == "order_rejected_by_broker"
