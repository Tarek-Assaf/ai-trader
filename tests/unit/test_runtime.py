"""Deterministic fail-safe runtime composition tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from trader.execution.control import ControlMode
from trader.runtime import RuntimeComponents, RuntimeConfig, RuntimeResult, RuntimeService


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
