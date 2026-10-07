"""Concrete service assembly for the demo runtime.

The API lifespan calls this module after settings and YAML validation.  Every
external dependency is explicit and the function raises before returning a
partial mapping, keeping startup fail closed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal

from trader.core.audit import SqlAuditWriter
from trader.core.settings import EnvironmentSettings
from trader.runtime.factory import RuntimeDependencyUnavailable

LOGGER = logging.getLogger(__name__)


def build_runtime_services(
    settings: EnvironmentSettings,
    configs: Mapping[str, object],
    *,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build DB/audit, paper and graph services, or fail before startup.

    Deployments may provide already-open DB and broker objects in ``overrides``
    (the unit fixture does this).  Production uses the configured database URL
    and explicit paper execution costs; no spread or broker value is guessed.
    The remaining stateful services are required overrides because their
    migrations and control leases belong to the process supervisor.
    """
    provided = dict(overrides or {})
    configured = configs.get("runtime_services")
    if isinstance(configured, Mapping):
        provided = {**dict(configured), **provided}

    redis_client = provided.get("redis_client")
    if redis_client is None:
        redis_url = settings.redis_url
        if redis_url is None or not redis_url.get_secret_value():
            raise RuntimeDependencyUnavailable("runtime service unavailable: redis")
        try:
            import redis

            redis_client = redis.Redis.from_url(redis_url.get_secret_value(), decode_responses=True)
        except Exception as exc:
            raise RuntimeDependencyUnavailable("redis client is unavailable") from exc

    engine = provided.get("engine")
    if engine is None:
        database_url = settings.database_url
        if database_url is None or not database_url.get_secret_value():
            raise RuntimeDependencyUnavailable("runtime service unavailable: database")
        try:
            from sqlalchemy import create_engine

            engine = create_engine(database_url.get_secret_value(), pool_pre_ping=True)
        except Exception as exc:
            raise RuntimeDependencyUnavailable("database engine is unavailable") from exc

    audit = provided.get("audit_writer") or SqlAuditWriter(engine)
    broker = provided.get("broker")
    if broker is None:
        paper = configs.get("paper_execution")
        if not isinstance(paper, Mapping):
            raise RuntimeDependencyUnavailable("runtime service unavailable: paper broker")
        try:
            from trader.execution.brokers.paper import PaperBroker, PaperExecutionCosts

            broker = PaperBroker(
                starting_cash=Decimal(str(paper["starting_cash"])),
                currency=str(paper["currency"]),
                costs=PaperExecutionCosts(
                    spread_points={
                        str(k): Decimal(str(v)) for k, v in dict(paper["spread_points"]).items()
                    },
                    slippage_points={
                        str(k): Decimal(str(v)) for k, v in dict(paper["slippage_points"]).items()
                    },
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeDependencyUnavailable("paper broker configuration is invalid") from exc

    from trader.execution.control import ControlMode
    from trader.execution.journal import ExecutionJournal
    from trader.execution.leader import RedisLeaderLease

    control_source = provided.get("control_source")
    control_writer = provided.get("control_writer")
    if control_source is None or control_writer is None:
        # Never default to an always-RUN control source: without the real
        # kill/pause channel the runtime must stay stopped (invariant 11).
        raise RuntimeDependencyUnavailable("runtime service unavailable: control channel")
    journal = provided.get("journal") or ExecutionJournal(engine)
    execution_lease = RedisLeaderLease(redis_client, "ai-trader:execution:lease")
    guardian_lease = RedisLeaderLease(redis_client, "ai-trader:guardian:lease")

    guardian = provided.get("guardian")
    if guardian is None:
        risk_values = _dump_mapping(configs.get("risk"))
        instruments = _dump_mapping(configs.get("instruments"))
        if not risk_values or not instruments:
            raise RuntimeDependencyUnavailable("runtime service unavailable: guardian")
        from trader.risk.guardian import RiskGuardian, RiskLimits

        symbols = instruments.get("instruments", instruments)
        guardian = RiskGuardian(
            RiskLimits.from_mapping(risk_values), audit, canonical_symbols=symbols.keys()
        )

    order_manager = provided.get("order_manager")
    if order_manager is None:
        from trader.execution.manager import BoundApproval, OrderManager
        from trader.risk.guardian import RiskDecision

        def approve(order: object, cycle_id: str) -> BoundApproval:
            return BoundApproval(
                order=order,
                cycle_id=cycle_id,
                decision=RiskDecision(
                    approved=False,
                    approved_size=None,
                    reasons=("runtime_guardian_context_required",),
                    rules_checked=(),
                ),
            )

        order_manager = OrderManager(
            broker,
            journal,
            execution_lease,
            guardian_lease,
            approve,
            lambda _positions: None,
            _log_alert,
            control_source,
            control_writer,
            lambda _positions: None,
        )

    reconciler = provided.get("reconciler") or _Reconciler(journal, broker)
    report_scheduler = provided.get("report_scheduler") or _ReportScheduler(configs.get("schedule"))
    settings_service = provided.get("settings_service")
    if settings_service is None:
        from trader.control.settings_service import SettingsService

        risk_values = _dump_mapping(configs.get("risk"))
        ceiling_values = _dump_mapping(configs.get("risk_ceilings"))
        if not risk_values or not ceiling_values:
            raise RuntimeDependencyUnavailable("runtime service unavailable: settings service")
        flat_values = {
            key: value
            for key, value in risk_values.items()
            if isinstance(value, int | float | Decimal)
        }
        settings_service = SettingsService(
            flat_values,
            ceiling_values,
            audit=audit,
            halt_active=lambda: control_source.read() is not ControlMode.RUN,
        )

    graph = provided.get("agent_graph")
    if graph is None:
        graph_builder = provided.get("agent_graph_builder")
        graph = (
            graph_builder(audit, guardian, order_manager)
            if callable(graph_builder)
            else _MockAgentGraph()
        )

    result = {
        "engine": engine,
        "audit_writer": audit,
        "broker": broker,
        "guardian": guardian,
        "order_manager": order_manager,
        "reconciler": reconciler,
        "report_scheduler": report_scheduler,
        "settings_service": settings_service,
        "control_source": control_source,
        "control_writer": control_writer,
        "journal": journal,
        "agent_graph": graph,
    }
    for optional in ("recorder", "session_calendar", "news_calendar"):
        if optional in provided:
            result[optional] = provided[optional]
    return result


def _log_alert(message: str) -> None:
    LOGGER.error("runtime alert: %s", message)


def _dump_mapping(value: object) -> dict[str, object]:
    method = getattr(value, "model_dump", None)
    raw = method() if callable(method) else value
    return dict(raw) if isinstance(raw, Mapping) else {}


class _Reconciler:
    def __init__(self, journal: object, broker: object) -> None:
        self.journal = journal
        self.broker = broker
        from trader.execution.reconciliation import reconcile_pnl

        self.reconcile_pnl = reconcile_pnl


class _ReportScheduler:
    def __init__(self, schedule: object) -> None:
        self.schedule = schedule
        from trader.reports.core import run_report

        self.run_report = run_report


class _MockAgentGraph:
    """Offline no-trade graph used until a deployment supplies a full graph."""

    def __init__(self) -> None:
        from trader.agents.llm.providers import MockLLM

        self.llm = MockLLM(())

    def run(self, cycle: object, *, executor: object) -> object:
        del executor
        from trader.runtime.composition import RuntimeResult

        return RuntimeResult.no_trade("mock_llm_no_trade", getattr(cycle, "event_id", None))


__all__ = ["build_runtime_services"]
