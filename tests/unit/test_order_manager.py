"""Offline execution failure and recovery checks."""

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from trader.core.db import tables
from trader.execution.brokers.base import Direction, OrderRequest
from trader.execution.brokers.paper import PaperBroker, PaperExecutionCosts
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

    def place_order(self, order):
        self.calls += 1
        receipt = super().place_order(order)
        if self.timeout:
            raise TimeoutError("accepted but reply lost")
        return receipt


def setup(tmp_path, *, pnl=None, approve=None):
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

    manager = OrderManager(
        broker,
        journal,
        lease,
        Lease(),
        approve or approval,
        pnl or (lambda positions: None),
        alerts.append,
    )
    manager.startup()
    order = OrderRequest(
        "cycle-1", "US100", Direction.BUY, Decimal("1"), Decimal("100"), Decimal("90")
    )
    return manager, broker, journal, lease, alerts, order


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
