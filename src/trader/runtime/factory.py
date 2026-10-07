"""Dependency-injected production composition for the demo core pod."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from trader.core.settings import EnvironmentSettings
from trader.execution.control import ControlSource
from trader.execution.leader import RedisLeaderLease
from trader.runtime.composition import RuntimeComponents


class RuntimeFactoryFn(Protocol):
    def __call__(self, *args: Any, **kwargs: Any) -> object: ...


@dataclass(frozen=True, slots=True)
class RuntimeFactories:
    """Validated constructors supplied by the deployment integration layer.

    Keeping external clients and database handles behind these callables lets
    the application compose the real services without importing broker code
    into the orchestration module.  Tests provide deterministic paper doubles.
    """

    redis_client: RuntimeFactoryFn
    broker: RuntimeFactoryFn
    guardian: RuntimeFactoryFn
    order_manager: RuntimeFactoryFn
    reconciler: RuntimeFactoryFn
    report_scheduler: RuntimeFactoryFn
    settings_service: RuntimeFactoryFn
    decision_loop: RuntimeFactoryFn
    lease_key: str = "ai-trader:core:lease"
    lease_ttl_ms: int = 30_000

    def __post_init__(self) -> None:
        if not self.lease_key.strip() or self.lease_ttl_ms <= 0:
            raise ValueError("runtime lease configuration is invalid")
        names = (
            "redis_client",
            "broker",
            "guardian",
            "order_manager",
            "reconciler",
            "report_scheduler",
            "settings_service",
            "decision_loop",
        )
        if any(not callable(getattr(self, name)) for name in names):
            raise TypeError("all runtime factories must be callable")


def build_runtime_components(
    settings: EnvironmentSettings,
    configs: Mapping[str, object],
    control_source: ControlSource,
    factories: RuntimeFactories,
) -> RuntimeComponents:
    """Build every core dependency, or raise before a partial runtime starts."""
    if (
        settings.role not in {"all", "core"}
        or settings.trading_mode not in {"paper", "auto"}
        or settings.broker_env != "demo"
        or settings.live_trading_enabled
    ):
        raise RuntimeError("runtime composition is only enabled for the demo core")

    redis_client = factories.redis_client(settings)
    # Duck typing is intentional because redis clients are third-party
    # implementations and RedisLeaseClient is a static-only protocol.
    if not all(callable(getattr(redis_client, name, None)) for name in ("set", "eval")):
        raise TypeError("redis client does not implement the lease protocol")
    leader = RedisLeaderLease(
        redis_client,
        factories.lease_key,
        ttl_ms=factories.lease_ttl_ms,
    )
    broker = factories.broker(settings, configs)
    guardian = factories.guardian(settings, configs)
    reconciler = factories.reconciler(settings, configs, broker)
    settings_service = factories.settings_service(settings, configs)
    report_scheduler = factories.report_scheduler(settings, configs)
    order_manager = factories.order_manager(
        settings,
        configs,
        broker,
        guardian,
        reconciler,
        control_source,
    )
    decision_loop = factories.decision_loop(
        settings,
        configs,
        order_manager,
        guardian,
        reconciler,
    )
    components = RuntimeComponents(
        leader=leader,
        decision_loop=decision_loop,
        guardian=guardian,
        broker=broker,
        order_manager=order_manager,
        reconciler=reconciler,
        report_scheduler=report_scheduler,
        settings_service=settings_service,
        metadata={"broker_env": settings.broker_env, "trading_mode": settings.trading_mode},
    )
    missing = components.missing_required()
    if missing:
        raise RuntimeError(f"runtime composition is incomplete: {', '.join(missing)}")
    return components


__all__ = ["RuntimeFactories", "build_runtime_components"]
