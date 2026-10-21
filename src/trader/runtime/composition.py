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
import re
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from trader.execution.control import ControlMode, ControlSource
from trader.runtime.soak import RuntimeSoakFeed

LOGGER = logging.getLogger(__name__)

_DEPENDENCY_MESSAGE_LIMIT = 200
_SENSITIVE_EXCEPTION_PATTERNS = (
    re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)=([^\s&]+)"),
    re.compile(r"(?i)(redis(?:s)?://)([^@\s]+)@"),
)


def _redacted_exception_message(exc: BaseException) -> str:
    """Return a bounded exception message with common credential forms removed."""
    message = " ".join(str(exc).split())
    for pattern in _SENSITIVE_EXCEPTION_PATTERNS:
        if pattern.pattern.startswith("(?i)(redis"):
            message = pattern.sub(r"\1[REDACTED]@", message)
        else:
            message = pattern.sub(r"\1=[REDACTED]", message)
    return message[:_DEPENDENCY_MESSAGE_LIMIT]


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


class ScheduleCycleSource:
    """Deterministic trigger queue for configured bar/session/news events.

    The source is deliberately fed by the market/calendar adapters.  It does
    not poll a broker or invent market data, which keeps a missing feed
    fail-safe while still giving the runtime one cycle id per trigger.
    """

    DEFAULT_BAR_CLOSES = ("1M", "3M", "5M")

    def __init__(self, events: Iterable[CycleTrigger] = ()) -> None:
        self._events: deque[CycleTrigger] = deque(events)

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> ScheduleCycleSource:
        """Build a source from a validated schedule mapping.

        ``config['triggers']`` may contain explicit event dictionaries.  When
        it is absent, the source starts empty and adapters submit the actual
        timestamped 1M/3M/5M bar, session-open and news triggers.
        """
        raw = config.get("triggers")
        if raw is None:
            return cls()
        if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
            raise ValueError("schedule triggers must be a sequence")
        events: list[CycleTrigger] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError("schedule trigger must be a mapping")
            at = item.get("at")
            if not isinstance(at, datetime):
                raise ValueError("schedule trigger timestamp must be datetime")
            events.append(
                CycleTrigger(
                    str(item.get("event", "")),
                    str(item.get("event_id", "")),
                    at,
                )
            )
        return cls(events)

    def emit(self, trigger: CycleTrigger) -> None:
        self._events.append(trigger)

    def next_cycle(self) -> CycleTrigger | None:
        return self._events.popleft() if self._events else None

    @property
    def pending(self) -> int:
        return len(self._events)


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
    soak_monitor: object | None = None
    autoscaler: object | None = None
    pnl_guard: object | None = None
    position_manager: object | None = None
    log_scanner: object | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def missing_required(self) -> tuple[str, ...]:
        """Return names that must be present before a core runtime can start."""
        required = {
            "guardian": self.guardian,
            "broker": self.broker,
            "order_manager": self.order_manager,
            "reconciler": self.reconciler,
            "report_scheduler": self.report_scheduler,
            "settings_service": self.settings_service,
        }
        return tuple(name for name, value in required.items() if value is None)


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
        lease_heartbeat_interval: float = 10.0,
    ) -> None:
        if backoff_initial <= 0 or backoff_max < backoff_initial:
            raise ValueError("backoff bounds are invalid")
        if lease_heartbeat_interval <= 0:
            raise ValueError("lease heartbeat interval must be positive")
        self.components = components
        self.control_source = control_source
        self._alert = alert or (lambda message: LOGGER.error("runtime alert: %s", message))
        self._cycle_source = cycle_source
        self._sleep = sleep
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._lease_heartbeat_interval = lease_heartbeat_interval
        self._last_soak_at: datetime | None = None
        self._soak_feed = RuntimeSoakFeed(
            {
                "guardian": components.guardian,
                "position_manager": getattr(components, "position_manager", None),
                "order_manager": components.order_manager,
                "pnl_guard": getattr(components, "pnl_guard", None),
                "reconciler": components.reconciler,
                "control": control_source,
                "log_scan": components.log_scanner,
            }
        )
        self._backoff = backoff_initial
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._lease_held = False
        self._last_dependency_failure: dict[str, str] | None = None
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
            self._dependency_call("leader.acquire", self.components.leader.acquire)
            self._lease_held = True
            self._dependency_call("leader.check", self.components.leader.check)
            reconcile = getattr(self.components.autoscaler, "reconcile_orphans", None)
            if callable(reconcile):
                reconcile()
        except Exception:
            self._lease_held = False
            self.state = RuntimeState.DEGRADED
            self._notify("runtime_leader_unavailable")
            if self._cycle_source is not None and self._task is None:
                self._task = asyncio.create_task(self.run_forever(), name="ai-trader-decision-loop")
            return
        self.state = RuntimeState.RUNNING
        self._soak_call("record_restart")
        self._start_lease_heartbeat()
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
        heartbeat_task, self._heartbeat_task = self._heartbeat_task, None
        if heartbeat_task is not None and heartbeat_task is not asyncio.current_task():
            heartbeat_task.cancel()
            try:
                await heartbeat_task
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
        if not self._lease_held:
            self.state = RuntimeState.DEGRADED
            self._notify("runtime_dependency_unavailable")
            return RuntimeResult.no_trade("runtime_dependency_unavailable", resolved_cycle_id)
        try:
            self._dependency_call("leader.check", self.components.leader.check)
            mode = self._dependency_call("control_source.read", self.control_source.read)
        except Exception:
            # The broad branch is intentional: a dependency failure must never
            # become an exception loop or permit an entry.
            self.state = RuntimeState.DEGRADED
            self._notify("runtime_dependency_unavailable")
            return RuntimeResult.no_trade("runtime_dependency_unavailable", resolved_cycle_id)
        if not self._lease_held or self._stop.is_set():
            self.state = RuntimeState.DEGRADED
            self._notify("runtime_dependency_unavailable")
            return RuntimeResult.no_trade("runtime_dependency_unavailable", resolved_cycle_id)
        if mode is not ControlMode.RUN:
            reason = "kill_switch_active" if mode is ControlMode.KILL else "execution_paused"
            return RuntimeResult.no_trade(reason, resolved_cycle_id)
        try:
            tai_runtime: Any = self.components.metadata.get("tai_demo_runtime")
            event_id = getattr(cycle, "event_id", "")
            if (
                tai_runtime is not None
                and isinstance(event_id, str)
                and event_id.startswith("bar:3M:")
            ):
                value = tai_runtime.run(cycle)
            else:
                value = self.components.decision_loop.run(cycle)
            if inspect.isawaitable(value):
                value = await value
            self._backoff = self._backoff_initial
            result = _result(value, resolved_cycle_id)
            self._record_soak(result)
            return result
        except Exception:
            self.state = RuntimeState.DEGRADED
            self._notify("runtime_cycle_failed")
            return RuntimeResult.no_trade("runtime_cycle_failed", resolved_cycle_id)

    async def run_forever(self) -> None:
        """Consume cycles with bounded retry backoff and no exception spin."""
        if self._cycle_source is None:
            return
        while not self._stop.is_set():
            if self.state is RuntimeState.DEGRADED:
                await self._wait(self._backoff)
                if self._stop.is_set():
                    break
                if await self._recover():
                    self._backoff = self._backoff_initial
                else:
                    self._backoff = min(self._backoff * 2, self._backoff_max)
                continue
            if not self.running:
                break
            try:
                run_due = getattr(self.components.report_scheduler, "run_due", None)
                if callable(run_due):
                    run_due(datetime.now(UTC))
                cycle = self._cycle_source.next_cycle()
                if inspect.isawaitable(cycle):
                    cycle = await cycle
                if cycle is None:
                    await self._wait(self._backoff)
                    continue
                result = await self.run_cycle(cycle)
                self._run_scaling()
                if result.reason in {"runtime_cycle_failed", "runtime_dependency_unavailable"}:
                    await self._wait(self._backoff)
                    self._backoff = min(self._backoff * 2, self._backoff_max)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._notify("runtime_cycle_source_failed")
                await self._wait(self._backoff)
                self._backoff = min(self._backoff * 2, self._backoff_max)

    async def _recover(self) -> bool:
        """Re-prove lease/control dependencies before resuming cycles."""
        if self._stop.is_set():
            return False
        try:
            if not self._lease_held:
                self._dependency_call("leader.acquire", self.components.leader.acquire)
                self._lease_held = True
            self._dependency_call("leader.check", self.components.leader.check)
            mode = self._dependency_call("control_source.read", self.control_source.read)
            if mode is ControlMode.KILL:
                self._notify("runtime_kill_switch_active")
                return False
            if self._stop.is_set():
                return False
        except Exception:
            self._lease_held = False
            self._notify("runtime_dependency_unavailable")
            return False
        self.state = RuntimeState.RUNNING
        self._start_lease_heartbeat()
        self._notify("runtime_recovered")
        return True

    def health_snapshot(self) -> dict[str, object]:
        """Return bounded runtime state for the liveness endpoint."""
        return {
            "state": self.state.value,
            "last_dependency_failure": dict(self._last_dependency_failure)
            if self._last_dependency_failure is not None
            else None,
        }

    def _dependency_call(self, stage: str, callback: Callable[[], Any]) -> Any:
        try:
            return callback()
        except Exception as exc:
            message = _redacted_exception_message(exc)
            self._last_dependency_failure = {
                "stage": stage,
                "error_type": type(exc).__name__,
                "message": message,
                "at": datetime.now(UTC).isoformat(),
            }
            LOGGER.error(
                "runtime dependency failed stage=%s error_type=%s error=%s",
                stage,
                type(exc).__name__,
                message,
            )
            raise

    def _start_lease_heartbeat(self) -> None:
        task = self._heartbeat_task
        if task is None or task.done():
            self._heartbeat_task = asyncio.create_task(
                self._lease_heartbeat_loop(),
                name="ai-trader-lease-heartbeat",
            )

    async def _lease_heartbeat_loop(self) -> None:
        """Renew the lease independently of market-bar arrival."""
        while not self._stop.is_set() and self._lease_held:
            await self._wait(self._lease_heartbeat_interval)
            if self._stop.is_set() or not self._lease_held:
                return
            try:
                self._dependency_call("leader.check", self.components.leader.check)
            except Exception:
                self._lease_held = False
                self.state = RuntimeState.DEGRADED
                self._notify("runtime_dependency_unavailable")
                return

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

    def _soak_call(self, method: str, *args: object, **kwargs: object) -> None:
        monitor = self.components.soak_monitor
        callback = getattr(monitor, method, None)
        if callable(callback):
            try:
                callback(*args, **kwargs)
            except Exception:
                LOGGER.warning("soak metric %s unavailable", method)

    def _record_soak(self, result: RuntimeResult) -> None:
        now = datetime.now(UTC)
        self._soak_call("record_cycle", at=now)
        if self._last_soak_at is not None:
            self._soak_call("record_uptime", (now - self._last_soak_at).total_seconds(), at=now)
        self._last_soak_at = now
        value = result.value
        status = getattr(value, "status", None)
        reason = getattr(value, "reason", None)
        if status is None:
            action_status = {
                "SUBMITTED": "submitted",
                "REJECTED": "rejected",
                "FAILED": "failed",
            }.get(result.action)
            status = action_status
        if reason is None:
            reason = result.reason
        component_observation = self._soak_feed.collect()
        if isinstance(value, Mapping):
            status = value.get("status", status)
            reason = value.get("reason", reason)
            observation = value.get("soak_observation")
        else:
            observation = getattr(value, "soak_observation", None)
        if isinstance(observation, Mapping):
            component_observation.update(observation)
        self._soak_call(
            "observe_cycle",
            component_observation,
            status=status if isinstance(status, str) else None,
            reason=reason if isinstance(reason, str) else None,
            at=now,
        )

    def _run_scaling(self) -> None:
        """Run optional worker scaling without letting it submit orders."""
        tick = getattr(self.components.autoscaler, "tick", None)
        if not callable(tick):
            return
        try:
            tick()
        except Exception:
            self._notify("runtime_scaling_failed")


def build_runtime(
    *,
    components: RuntimeComponents,
    control_source: ControlSource,
    alert: Callable[[str], None] | None = None,
    cycle_source: CycleSource | None = None,
    lease_heartbeat_interval: float = 10.0,
) -> RuntimeService:
    """Build the single core runtime from already validated dependencies."""
    return RuntimeService(
        components,
        control_source=control_source,
        alert=alert,
        cycle_source=cycle_source,
        lease_heartbeat_interval=lease_heartbeat_interval,
    )


__all__ = [
    "CycleTrigger",
    "Lease",
    "RuntimeComponents",
    "RuntimeConfig",
    "RuntimeResult",
    "RuntimeService",
    "RuntimeState",
    "ScheduleCycleSource",
    "build_runtime",
    "runtime_enabled",
]
