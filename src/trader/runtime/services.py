"""Concrete service assembly for the demo runtime.

The API lifespan calls this module after settings and YAML validation.  Every
external dependency is explicit and the function raises before returning a
partial mapping, keeping startup fail closed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

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
    Stateful services are built from the validated database, Redis and risk
    configuration.  Overrides remain available for deterministic fixtures and
    deployment-owned clients.
    """
    provided: dict[str, Any] = dict(overrides or {})
    configured = configs.get("runtime_services")
    if isinstance(configured, Mapping):
        provided = {**dict(configured), **provided}

    redis_client: Any = provided.get("redis_client")
    if redis_client is None:
        redis_url = settings.redis_url
        if redis_url is None or not redis_url.get_secret_value():
            raise RuntimeDependencyUnavailable("runtime service unavailable: redis")
        try:
            import redis

            redis_client = redis.Redis.from_url(redis_url.get_secret_value(), decode_responses=True)
        except Exception as exc:
            raise RuntimeDependencyUnavailable("redis client is unavailable") from exc

    engine: Any = provided.get("engine")
    if engine is None:
        database_url = settings.database_url
        if database_url is None or not database_url.get_secret_value():
            raise RuntimeDependencyUnavailable("runtime service unavailable: database")
        try:
            from sqlalchemy import create_engine

            engine = create_engine(database_url.get_secret_value(), pool_pre_ping=True)
        except Exception as exc:
            raise RuntimeDependencyUnavailable("database engine is unavailable") from exc

    audit: Any = provided.get("audit_writer") or SqlAuditWriter(engine)
    broker: Any = provided.get("broker")
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
    from trader.runtime.approval import (
        PnlLossGuard,
        ProtectivePositionManager,
        RuntimeApproval,
        RuntimeExecutor,
        RuntimeReconciler,
    )
    from trader.runtime.context import (
        AccountContextSource,
        MarketContextSource,
        RuntimeContextProvider,
        SizingContextSource,
        position_source,
    )

    control_source: Any = provided.get("control_source")
    control_writer: Any = provided.get("control_writer")
    if control_source is None or control_writer is None:
        # Never default to an always-RUN control source: without the real
        # kill/pause channel the runtime must stay stopped (invariant 11).
        raise RuntimeDependencyUnavailable("runtime service unavailable: control channel")
    journal: Any = provided.get("journal") or ExecutionJournal(engine)
    execution_lease = RedisLeaderLease(redis_client, "ai-trader:execution:lease")
    guardian_lease = RedisLeaderLease(redis_client, "ai-trader:guardian:lease")

    guardian: Any = provided.get("guardian")
    if guardian is None:
        risk_values = _dump_mapping(configs.get("risk"))
        instruments = _dump_mapping(configs.get("instruments"))
        if not risk_values or not instruments:
            raise RuntimeDependencyUnavailable("runtime service unavailable: guardian")
        from trader.risk.guardian import RiskGuardian, RiskLimits

        symbols: Any = instruments.get("instruments", instruments)
        guardian = RiskGuardian(
            RiskLimits.from_mapping(risk_values), audit, canonical_symbols=symbols.keys()
        )

    approval_adapter: Any = provided.get("approval")
    order_manager: Any = provided.get("order_manager")
    context_provider: Any = provided.get("guardian_context_provider")
    if context_provider is None:
        account_source = provided.get("account_context_source")
        if account_source is None:
            account_source = AccountContextSource(broker, journal, clock=lambda: datetime.now(UTC))
        market_source = provided.get("market_context_source")
        if market_source is None and provided.get("recorder") is not None:
            market_source = MarketContextSource(
                provided["recorder"],
                clock=lambda: datetime.now(UTC),
                stale_after_seconds=float(provided.get("market_stale_after_seconds", 180.0)),
            )
        sizing_source = provided.get("sizing_context_source")
        if sizing_source is None:
            mapper = provided.get("instrument_mapper")
            limits = provided.get("sizing_limits")
            currency = provided.get("account_currency_provider")
            if mapper is not None and limits is not None and callable(currency):
                sizing_source = SizingContextSource(
                    mapper.get,
                    limits,
                    account_currency=currency,
                    quote_rate_provider=provided.get("quote_rate_provider"),
                )
        positions_source = provided.get("positions_provider") or position_source(
            broker, valuation=provided.get("position_valuation")
        )
        if callable(account_source) and callable(market_source) and callable(sizing_source):
            context_provider = RuntimeContextProvider(
                account_source, market_source, sizing_source, positions_source
            )
        else:

            def context_provider(*_args: Any) -> None:
                return None

    if order_manager is None:
        from trader.execution.manager import OrderManager

        risk_values = _dump_mapping(configs.get("risk"))
        try:
            daily_loss = Decimal(str(risk_values["max_daily_loss_pct"]))
            weekly_loss = Decimal(str(risk_values["max_weekly_loss_pct"]))
        except (KeyError, TypeError, ValueError):
            raise RuntimeDependencyUnavailable("runtime service unavailable: pnl guard") from None
        approval = approval_adapter or RuntimeApproval(guardian, context_provider=context_provider)
        approval_adapter = approval
        pnl_guard = PnlLossGuard(
            broker,
            max_daily_loss_pct=daily_loss,
            max_weekly_loss_pct=weekly_loss,
            control_writer=control_writer,
            alert=_log_alert,
            journal=journal,
        )
        position_manager = ProtectivePositionManager(
            broker, control_writer=control_writer, alert=_log_alert
        )

        order_manager = OrderManager(
            broker,
            journal,
            execution_lease,
            guardian_lease,
            approval,
            pnl_guard,
            _log_alert,
            control_source,
            control_writer,
            position_manager,
        )

    reconciler: Any = provided.get("reconciler") or RuntimeReconciler(
        journal, broker, alert=_log_alert
    )
    report_scheduler: Any = provided.get("report_scheduler") or _ReportScheduler(
        configs.get("schedule")
    )
    settings_service: Any = provided.get("settings_service")
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

    graph: Any = provided.get("agent_graph")
    if graph is None:
        graph_builder = provided.get("agent_graph_builder")
        if callable(graph_builder):
            graph = graph_builder(audit, guardian, order_manager)
        else:
            from trader.agents.graph import AgentGraph
            from trader.agents.llm.providers import MockLLM

            # MockLLM has no scripted output by default, so an unattended pod
            # fails closed.  Tests/deployments may supply a scripted provider
            # or a graph builder without bypassing the approval/executor gate.
            llm: Any = provided.get("llm") or MockLLM(())
            graph = AgentGraph(
                llm=llm,
                audit=audit,
                guardian=approval_adapter
                or RuntimeApproval(guardian, context_provider=context_provider),
                executor=RuntimeExecutor(order_manager),
            )

    result = {
        "engine": engine,
        "audit_writer": audit,
        "broker": broker,
        "guardian": guardian,
        "approval": approval_adapter,
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


class _ReportScheduler:
    def __init__(self, schedule: object) -> None:
        self.schedule = schedule
        from trader.reports.core import run_report

        self.run_report = run_report


__all__ = ["build_runtime_services"]
