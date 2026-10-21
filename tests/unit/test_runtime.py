"""Deterministic fail-safe runtime composition tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from trader.execution.control import ControlMode
from trader.runtime import (
    CycleTrigger,
    RuntimeComponents,
    RuntimeConfig,
    RuntimeResult,
    RuntimeService,
    ScheduleCycleSource,
)


class FakeLease:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.acquires = 0
        self.checks = 0
        self.releases = 0

    def acquire(self) -> None:
        self.acquires += 1
        if self.fail:
            raise OSError("redis unavailable")

    def check(self) -> None:
        self.checks += 1
        if self.fail:
            raise OSError("redis unavailable")

    def release(self) -> None:
        self.releases += 1


class FakeControl:
    def __init__(self, mode: ControlMode = ControlMode.RUN) -> None:
        self.mode = mode

    def read(self) -> ControlMode:
        return self.mode


@dataclass
class Cycle:
    cycle_id: str = "cycle-1"


class FakeLoop:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, cycle: Cycle) -> RuntimeResult:
        self.calls += 1
        return RuntimeResult("NO_TRADE", "mock_cycle", cycle.cycle_id)


def service(*, lease: FakeLease | None = None, mode: ControlMode = ControlMode.RUN):
    lease = lease or FakeLease()
    loop = FakeLoop()
    return (
        RuntimeService(
            RuntimeComponents(leader=lease, decision_loop=loop),
            control_source=FakeControl(mode),
        ),
        lease,
        loop,
    )


def test_runtime_gate_is_demo_core_only() -> None:
    assert RuntimeConfig().enabled
    assert RuntimeConfig(role="worker").enabled is False
    assert RuntimeConfig(broker_env="live").enabled is False
    assert RuntimeConfig(live_trading_enabled=True).enabled is False


def test_components_report_missing_core_dependencies() -> None:
    components = RuntimeComponents(leader=FakeLease(), decision_loop=FakeLoop())
    assert components.missing_required() == (
        "guardian",
        "broker",
        "order_manager",
        "reconciler",
        "report_scheduler",
        "settings_service",
    )


def test_cycle_requires_leader_and_control_then_runs_loop() -> None:
    async def run() -> None:
        runtime, lease, loop = service()
        await runtime.start()
        result = await runtime.run_cycle(Cycle())
        assert result.reason == "mock_cycle"
        assert loop.calls == 1
        assert lease.acquires == 1
        assert lease.checks >= 2
        await runtime.stop()
        assert lease.releases == 1

    asyncio.run(run())


def test_pause_and_kill_are_no_trade_without_calling_loop() -> None:
    async def run() -> None:
        runtime, _lease, loop = service(mode=ControlMode.PAUSE)
        await runtime.start()
        assert (await runtime.run_cycle(Cycle())).reason == "execution_paused"
        assert loop.calls == 0
        await runtime.stop()

    asyncio.run(run())


def test_redis_failure_degrades_and_never_runs_the_loop() -> None:
    async def run() -> None:
        runtime, _lease, loop = service(lease=FakeLease(fail=True))
        alerts: list[str] = []
        runtime._alert = alerts.append
        await runtime.start()
        assert runtime.running is False
        assert alerts == ["runtime_leader_unavailable"]
        assert (await runtime.run_cycle(Cycle())).reason == "runtime_not_running"
        assert loop.calls == 0

    asyncio.run(run())


def test_degraded_runtime_recovers_after_dependencies_return() -> None:
    async def run() -> None:
        lease = FakeLease(fail=True)
        runtime, _lease, loop = service(lease=lease)
        alerts: list[str] = []
        runtime._alert = alerts.append
        await runtime.start()
        assert runtime.state.value == "degraded"
        lease.fail = False
        assert await runtime._recover() is True
        assert (await runtime.run_cycle(Cycle())).reason == "mock_cycle"
        assert loop.calls == 1
        assert "runtime_recovered" in alerts
        await runtime.stop()

    asyncio.run(run())


def test_schedule_source_preserves_trigger_order_and_cycle_ids() -> None:
    first = CycleTrigger.now("bar_close", "bar-1")
    second = CycleTrigger.now("session_open", "session-1")
    source = ScheduleCycleSource.from_config(
        {
            "triggers": [
                {"event": first.event, "event_id": first.event_id, "at": first.at},
                {"event": second.event, "event_id": second.event_id, "at": second.at},
            ]
        }
    )
    assert source.next_cycle() == first
    assert source.next_cycle() == second
    assert source.next_cycle() is None


def test_second_core_cannot_acquire_shared_lease() -> None:
    class SharedLease(FakeLease):
        held = False

        def acquire(self) -> None:
            if type(self).held:
                raise OSError("lease held elsewhere")
            type(self).held = True
            super().acquire()

        def release(self) -> None:
            type(self).held = False
            super().release()

    async def run() -> None:
        first, first_lease, _ = service(lease=SharedLease())
        second, _second_lease, _ = service(lease=SharedLease())
        await first.start()
        await second.start()
        assert first.running is True
        assert second.running is False
        assert (await second.run_cycle(Cycle())).reason == "runtime_not_running"
        await first.stop()
        assert first_lease.releases == 1

    asyncio.run(run())


def test_replay_produces_identical_cycle_results() -> None:
    triggers = [
        CycleTrigger.now("bar_close", "bar-1"),
        CycleTrigger.now("bar_close", "bar-2"),
    ]

    async def replay() -> list[tuple[str | None, str]]:
        source = ScheduleCycleSource(triggers)
        runtime, _lease, _loop = service()
        await runtime.start()
        results: list[tuple[str | None, str]] = []
        while (cycle := source.next_cycle()) is not None:
            result = await runtime.run_cycle(cycle)
            results.append((result.cycle_id, result.reason))
        await runtime.stop()
        return results

    async def run() -> None:
        assert await replay() == await replay()

    asyncio.run(run())


def test_cycle_failure_alerts_once_and_returns_no_trade() -> None:
    class FailingLoop:
        def run(self, _cycle: Cycle) -> RuntimeResult:
            raise OSError("paper broker unavailable")

    async def run() -> None:
        lease = FakeLease()
        alerts: list[str] = []
        runtime = RuntimeService(
            RuntimeComponents(leader=lease, decision_loop=FailingLoop()),
            control_source=FakeControl(),
            alert=alerts.append,
        )
        await runtime.start()
        result = await runtime.run_cycle(Cycle())
        assert result.action == "NO_TRADE"
        assert result.reason == "runtime_cycle_failed"
        assert alerts == ["runtime_cycle_failed"]
        await runtime.stop()

    asyncio.run(run())


def test_lease_heartbeat_detects_expiry_between_bars() -> None:
    async def run() -> None:
        lease = FakeLease()
        runtime = RuntimeService(
            RuntimeComponents(leader=lease, decision_loop=FakeLoop()),
            control_source=FakeControl(),
            lease_heartbeat_interval=0.001,
        )
        await runtime.start()
        lease.fail = True
        await asyncio.sleep(0.01)
        assert runtime.state.value == "degraded"
        assert lease.checks >= 2
        await runtime.stop()

    asyncio.run(run())


def test_lease_heartbeat_failure_blocks_entries() -> None:
    async def run() -> None:
        lease = FakeLease()
        loop = FakeLoop()
        runtime = RuntimeService(
            RuntimeComponents(leader=lease, decision_loop=loop),
            control_source=FakeControl(),
            lease_heartbeat_interval=0.001,
        )
        await runtime.start()
        lease.fail = True
        await asyncio.sleep(0.01)
        result = await runtime.run_cycle(Cycle())
        assert result.reason == "runtime_not_running"
        assert loop.calls == 0
        await runtime.stop()

    asyncio.run(run())
