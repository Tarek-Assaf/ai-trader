"""Dependency-injected production composition for the demo core pod."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeGuard

from trader.core.accounts import scoped_lease_key
from trader.core.audit import AuditEvent
from trader.core.settings import EnvironmentSettings
from trader.execution.control import ControlSource
from trader.execution.leader import RedisLeaderLease
from trader.runtime.composition import RuntimeComponents, RuntimeResult
from trader.scaling.runtime import build_core_autoscaler


class RuntimeDependencyUnavailable(RuntimeError):
    """A required production dependency is not available at startup."""


class OrderManagerDecisionLoop:
    """Small adapter that keeps every decision cycle behind OrderManager.

    The agent graph is supplied by deployments as ``decision_loop``.  This
    adapter is useful for the default composition and for the paper fixture:
    it never calls a broker itself and reports a deterministic no-trade result
    until the graph has a cycle runner.
    """

    def __init__(
        self,
        order_manager: object,
        graph: object | None = None,
        audit_writer: object | None = None,
    ) -> None:
        self.order_manager = order_manager
        self.graph = graph
        self.audit_writer = audit_writer

    def run(self, cycle: object) -> object:
        runner = getattr(self.graph, "run", None)
        if callable(runner):
            # Legacy fixture graphs accepted an executor keyword, while the
            # real AgentGraph owns its Guardian and executor adapters.  Keep
            # both contracts without swallowing errors from the graph itself.
            parameters: Any
            try:
                parameters = inspect.signature(runner).parameters
            except (TypeError, ValueError):
                parameters = {}
            result = (
                runner(cycle, executor=self.order_manager)
                if "executor" in parameters
                else runner(cycle)
            )
        else:
            result = RuntimeResult.no_trade(
                "decision_graph_unavailable", getattr(cycle, "event_id", None)
            )
        append = getattr(self.audit_writer, "append", None)
        if callable(append):
            cycle_id = getattr(cycle, "event_id", None)
            append(
                AuditEvent(
                    event_type="runtime.cycle",
                    cycle_id=cycle_id if isinstance(cycle_id, str) else None,
                    payload={"action": getattr(result, "action", "NO_TRADE")},
                )
            )
        return result


class RuntimeFactoryFn(Protocol):
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


def _is_explicit_factory(value: object) -> TypeGuard[RuntimeFactoryFn]:
    """Return whether a supplied value is a function intended as a factory.

    Runtime services such as ``PnlLossGuard`` and
    ``ProtectivePositionManager`` are callable instances because they are
    invoked during execution. Treating every callable as a constructor
    invokes those instances during startup with factory arguments. Only
    routines supplied as overrides are explicit factories; all other objects
    are constants.
    """

    return inspect.isroutine(value)


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
    pnl_guard: RuntimeFactoryFn | None = None
    position_manager: RuntimeFactoryFn | None = None
    log_scanner: RuntimeFactoryFn | None = None
    lease_key: str = field(default_factory=lambda: scoped_lease_key("ai-trader:core:lease"))
    lease_ttl_ms: int = 30_000
    autoscaler: RuntimeFactoryFn | None = None

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
    pnl_guard = (
        factories.pnl_guard(settings, configs, broker) if callable(factories.pnl_guard) else None
    )
    position_manager = (
        factories.position_manager(settings, configs, broker)
        if callable(factories.position_manager)
        else None
    )
    log_scanner = (
        factories.log_scanner(settings, configs) if callable(factories.log_scanner) else None
    )
    decision_loop = factories.decision_loop(
        settings,
        configs,
        order_manager,
        guardian,
        reconciler,
    )
    autoscaler = (
        factories.autoscaler(settings, configs)
        if factories.autoscaler is not None
        else build_core_autoscaler(settings, configs, redis_client)
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
        autoscaler=autoscaler,
        pnl_guard=pnl_guard,
        position_manager=position_manager,
        log_scanner=log_scanner,
        metadata={"broker_env": settings.broker_env, "trading_mode": settings.trading_mode},
    )
    missing = components.missing_required()
    if missing:
        raise RuntimeError(f"runtime composition is incomplete: {', '.join(missing)}")
    return components


def build_default_factories(
    settings: EnvironmentSettings,
    configs: Mapping[str, object],
    *,
    services: Mapping[str, object] | None = None,
) -> RuntimeFactories:
    """Build the concrete paper/demo providers used by the application.

    Database-backed services are deployment-owned, so they are passed through
    the ``runtime_services`` mapping after migrations and leases are ready.
    The function still constructs Redis and the paper broker itself when their
    explicit configuration is present, and fails closed when a service is
    missing rather than starting a partial runtime.
    """
    supplied: dict[str, object] = {}
    configured = configs.get("runtime_services")
    if isinstance(configured, Mapping):
        supplied.update(configured)
    if services is not None:
        supplied.update(services)

    def service(name: str) -> RuntimeFactoryFn:
        """Adapt an injected service instance to the factory contract.

        A service object may itself be callable during normal runtime
        operation. Only a function or method is an explicit constructor
        override; injected objects are returned unchanged by the adapter.
        """

        value = supplied.get(name)
        if value is None:

            def missing(*_args: Any, **_kwargs: Any) -> Any:
                raise RuntimeDependencyUnavailable(f"runtime service unavailable: {name}")

            return missing
        if _is_explicit_factory(value):
            return value
        return lambda *_args, _value=value, **_kwargs: _value

    def dump(value: object) -> object:
        model_dump = getattr(value, "model_dump", None)
        return model_dump() if callable(model_dump) else value

    def guardian(
        current_settings: EnvironmentSettings,
        current_configs: Mapping[str, object],
    ) -> Any:
        if "guardian" in supplied:
            value = supplied["guardian"]
            return (
                value(current_settings, current_configs) if _is_explicit_factory(value) else value
            )
        audit: Any = supplied.get("audit_writer")
        risk_values = dump(current_configs.get("risk"))
        instruments = dump(current_configs.get("instruments"))
        if audit is None or not isinstance(risk_values, Mapping):
            raise RuntimeDependencyUnavailable("runtime service unavailable: guardian")
        try:
            from trader.risk.guardian import RiskGuardian, RiskLimits

            symbols = (
                instruments.get("instruments", instruments)
                if isinstance(instruments, Mapping)
                else {}
            )
            if not isinstance(symbols, Mapping) or not symbols:
                raise ValueError("instrument symbols are missing")
            return RiskGuardian(
                RiskLimits.from_mapping(risk_values),
                audit,
                canonical_symbols=symbols.keys(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeDependencyUnavailable("risk guardian configuration is invalid") from exc

    def redis_client(current_settings: EnvironmentSettings) -> Any:
        if "redis_client" in supplied:
            value = supplied["redis_client"]
            return value(current_settings) if _is_explicit_factory(value) else value
        url = current_settings.redis_url
        if url is None or not url.get_secret_value():
            raise RuntimeDependencyUnavailable("runtime service unavailable: redis_client")
        try:
            import redis
        except ImportError:
            raise RuntimeDependencyUnavailable("redis package is unavailable") from None
        return redis.Redis.from_url(url.get_secret_value(), decode_responses=True)

    def broker(current_settings: EnvironmentSettings, current_configs: Mapping[str, object]) -> Any:
        if "broker" in supplied:
            value = supplied["broker"]
            return (
                value(current_settings, current_configs) if _is_explicit_factory(value) else value
            )
        paper = dump(current_configs.get("paper_execution"))
        if not isinstance(paper, Mapping):
            raise RuntimeDependencyUnavailable("runtime service unavailable: broker")
        try:
            from decimal import Decimal

            from trader.execution.brokers.paper import PaperBroker, PaperExecutionCosts

            costs = PaperExecutionCosts(
                spread_points={
                    str(k): Decimal(str(v)) for k, v in dict(paper["spread_points"]).items()
                },
                slippage_points={
                    str(k): Decimal(str(v)) for k, v in dict(paper["slippage_points"]).items()
                },
            )
            return PaperBroker(
                starting_cash=Decimal(str(paper["starting_cash"])),
                currency=str(paper["currency"]),
                costs=costs,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeDependencyUnavailable("paper broker configuration is invalid") from exc

    def decision_loop(
        current_settings: EnvironmentSettings,
        current_configs: Mapping[str, object],
        order_manager: Any,
        guardian: Any,
        reconciler: Any,
    ) -> Any:
        custom = supplied.get("decision_loop")
        if _is_explicit_factory(custom):
            return custom(current_settings, current_configs, order_manager, guardian, reconciler)
        return OrderManagerDecisionLoop(
            order_manager,
            supplied.get("agent_graph"),
            supplied.get("audit_writer"),
        )

    autoscaler_factory = service("autoscaler") if "autoscaler" in supplied else None
    return RuntimeFactories(
        redis_client=redis_client,
        broker=broker,
        guardian=guardian,
        order_manager=service("order_manager"),
        reconciler=service("reconciler"),
        report_scheduler=service("report_scheduler"),
        settings_service=service("settings_service"),
        decision_loop=decision_loop,
        autoscaler=autoscaler_factory,
        pnl_guard=service("pnl_guard") if "pnl_guard" in supplied else None,
        position_manager=service("position_manager") if "position_manager" in supplied else None,
        log_scanner=service("log_scanner") if "log_scanner" in supplied else None,
    )


__all__ = [
    "OrderManagerDecisionLoop",
    "RuntimeDependencyUnavailable",
    "RuntimeFactories",
    "build_default_factories",
    "build_runtime_components",
]
