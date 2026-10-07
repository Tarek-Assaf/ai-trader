"""Compose and run the safety-critical demo runtime.

The runtime deliberately owns orchestration only.  It never manufactures an
order and never calls a broker directly: an injected decision loop must route
entries through :class:`trader.execution.manager.OrderManager`, where the
Guardian and durable intent reservation are enforced.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from trader.execution.control import ControlMode, ControlSource

LOGGER = logging.getLogger(__name__)


class RuntimeState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Environment gates for starting a trading runtime."""

    role: str = "all"
    trading_mode: str = "paper"
    broker_env: str = "demo"
    live_trading_enabled: bool = False

    @property
    def enabled(self) -> bool:
        """Only core/all demo processes may run the decision loop."""
        return (
            self.role in {"all", "core"}
            and self.trading_mode in {"paper", "auto"}
            and self.broker_env == "demo"
            and not self.live_trading_enabled
        )


def runtime_enabled(settings: object) -> bool:
    """Return the fail-closed startup decision for an EnvironmentSettings object."""
    return RuntimeConfig(
        role=str(getattr(settings, "role", "")),
        trading_mode=str(getattr(settings, "trading_mode", "")),
        broker_env=str(getattr(settings, "broker_env", "")),
        live_trading_enabled=bool(getattr(settings, "live_trading_enabled", True)),
    ).enabled


@dataclass(frozen=True, slots=True)
class CycleTrigger:
    """A normalized event delivered to the decision-loop cycle source."""

    event: str
    event_id: str
    at: datetime

    def __post_init__(self) -> None:
        if self.event not in {"bar_close", "session_open", "news_event"}:
            raise ValueError("unsupported runtime event")
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("trigger timestamp must be timezone-aware")

    @classmethod
    def now(cls, event: str, event_id: str | None = None) -> CycleTrigger:
        return cls(event, event_id or str(uuid4()), datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    """Sanitized outcome returned by one runtime cycle."""

    action: str
    reason: str
    cycle_id: str | None = None
    value: Any = None

    @classmethod
    def no_trade(cls, reason: str, cycle_id: str | None = None) -> RuntimeResult:
        return cls("NO_TRADE", reason, cycle_id)


@runtime_checkable
class Lease(Protocol):
    def acquire(self) -> None: ...

    def check(self) -> None: ...

    def release(self) -> None: ...


class CycleRunner(Protocol):
    def run(self, cycle: object) -> object | Awaitable[object]: ...


class CycleSource(Protocol):
    def next_cycle(self) -> object | Awaitable[object | None] | None: ...


@dataclass(slots=True)
class RuntimeComponents:
    """Explicit composition boundary for the services owned by the core pod."""

    leader: Lease
    decision_loop: CycleRunner
    guardian: object | None = None
    broker: object | None = None
    order_manager: object | None = None
    reconciler: object | None = None
    report_scheduler: object | None = None
    settings_service: object | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


def _result(value: object, cycle_id: str | None) -> RuntimeResult:
    if isinstance(value, RuntimeResult):
        return value
    action = getattr(value, "action", None) or getattr(value, "status", None)
    if not isinstance(action, str):
        action = "NO_TRADE"
    reason = getattr(value, "reason", None)
    if not isinstance(reason, str) or not reason:
        reason = "cycle_completed" if action != "NO_TRADE" else "no_trade"
    return RuntimeResult(action, reason, cycle_id, value)


class RuntimeService:
    """Run cycles while holding the Redis leader lease and fail closed.

    ``decision_loop`` is intentionally injected.  A production composition
    supplies an ``AgentGraph`` adapter whose executor is ``OrderManager``;
    tests can supply a deterministic replay loop without network services.
    """

    def __init__(
        self,
        components: RuntimeComponents,
        *,
        control_source: ControlSource,
        alert: Callable[[str], None] | None = None,
        cycle_source: CycleSource | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff_initial: float = 1.0,
        backoff_max: float = 60.0,
    ) -> None:
        if backoff_initial <= 0 or backoff_max < backoff_initial:
            raise ValueError("backoff bounds are invalid")
        self.components = components
        self.control_source = control_source
        self._alert = alert or (lambda message: LOGGER.error("runtime alert: %s", message))
        self._cycle_source = cycle_source
        self._sleep = sleep
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._backoff = backoff_initial
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._lease_held = False
        self.state = RuntimeState.STOPPED

    @property
    def running(self) -> bool:
        return self.state is RuntimeState.RUNNING

    async def start(self) -> None:
        """Acquire leadership before accepting cycles; never start without proof."""
        if self.running:
            return
        self.state = RuntimeState.STARTING
        self._stop.clear()
        try:
            self.components.leader.acquire()
            self._lease_held = True
            self.components.leader.check()
        except Exception:
            self._lease_held = False
            self.state = RuntimeState.DEGRADED
            self._notify("runtime_leader_unavailable")
            return
        self.state = RuntimeState.RUNNING
        if self._cycle_source is not None:
            self._task = asyncio.create_task(self.run_forever(), name="ai-trader-decision-loop")

    async def stop(self) -> None:
        """Stop the cycle task and release leadership without masking shutdown."""
        self._stop.set()
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._lease_held:
            try:
                self.components.leader.release()
            except Exception:
                self._notify("runtime_leader_release_failed")
            finally:
                self._lease_held = False
        self.state = RuntimeState.STOPPED

    async def run_cycle(self, cycle: object, *, cycle_id: str | None = None) -> RuntimeResult:
        """Run exactly one cycle under the lease and external kill/pause controls."""
        resolved_cycle_id = cycle_id or getattr(cycle, "cycle_id", None)
        if not self.running:
            return RuntimeResult.no_trade("runtime_not_running", resolved_cycle_id)
        try:
            self.components.leader.check()
            mode = self.control_source.read()
        except Exception:
            # The broad branch is intentional: a dependency failure must never
            # become an exception loop or permit an entry.
            self.state = RuntimeState.DEGRADED
            self._notify("runtime_dependency_unavailable")
            return RuntimeResult.no_trade("runtime_dependency_unavailable", resolved_cycle_id)
        if mode is not ControlMode.RUN:
            reason = "kill_switch_active" if mode is ControlMode.KILL else "execution_paused"
            return RuntimeResult.no_trade(reason, resolved_cycle_id)
        try:
            value = self.components.decision_loop.run(cycle)
            if inspect.isawaitable(value):
                value = await value
            self._backoff = self._backoff_initial
            return _result(value, resolved_cycle_id)
        except Exception:
            self.state = RuntimeState.DEGRADED
            self._notify("runtime_cycle_failed")
            return RuntimeResult.no_trade("runtime_cycle_failed", resolved_cycle_id)

    async def run_forever(self) -> None:
        """Consume cycles with bounded retry backoff and no exception spin."""
        if self._cycle_source is None:
            return
        while not self._stop.is_set() and self.running:
            try:
                cycle = self._cycle_source.next_cycle()
                if inspect.isawaitable(cycle):
                    cycle = await cycle
                if cycle is None:
                    await self._wait(self._backoff)
                    continue
                result = await self.run_cycle(cycle)
                if result.reason in {"runtime_cycle_failed", "runtime_dependency_unavailable"}:
                    await self._wait(self._backoff)
                    self._backoff = min(self._backoff * 2, self._backoff_max)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._notify("runtime_cycle_source_failed")
                await self._wait(self._backoff)
                self._backoff = min(self._backoff * 2, self._backoff_max)

    async def _wait(self, seconds: float) -> None:
        try:
            await self._sleep(seconds)
        except asyncio.CancelledError:
            raise

    def _notify(self, message: str) -> None:
        try:
            self._alert(message)
        except Exception:
            LOGGER.exception("runtime alert delivery failed")


def build_runtime(
    *,
    components: RuntimeComponents,
    control_source: ControlSource,
    alert: Callable[[str], None] | None = None,
    cycle_source: CycleSource | None = None,
) -> RuntimeService:
    """Build the single core runtime from already validated dependencies."""
    return RuntimeService(
        components,
        control_source=control_source,
        alert=alert,
        cycle_source=cycle_source,
    )


__all__ = [
    "CycleTrigger",
    "Lease",
    "RuntimeComponents",
    "RuntimeConfig",
    "RuntimeResult",
    "RuntimeService",
    "RuntimeState",
    "build_runtime",
    "runtime_enabled",
]
