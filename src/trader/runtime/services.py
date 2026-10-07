"""Concrete service assembly for the demo runtime.

The API lifespan calls this module after settings and YAML validation.  Every
external dependency is explicit and the function raises before returning a
partial mapping, keeping startup fail closed.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from trader.core.audit import SqlAuditWriter
from trader.core.settings import EnvironmentSettings
from trader.runtime.factory import RuntimeDependencyUnavailable


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

    required = (
        "guardian",
        "order_manager",
        "reconciler",
        "report_scheduler",
        "settings_service",
    )
    missing = [name for name in required if provided.get(name) is None]
    if missing:
        raise RuntimeDependencyUnavailable("runtime services unavailable: " + ", ".join(missing))

    graph = provided.get("agent_graph")
    if graph is None:
        graph_builder = provided.get("agent_graph_builder")
        if not callable(graph_builder):
            raise RuntimeDependencyUnavailable("runtime service unavailable: agent graph")
        graph = graph_builder(audit, provided["guardian"], provided["order_manager"])

    result = {
        "engine": engine,
        "audit_writer": audit,
        "broker": broker,
        "guardian": provided["guardian"],
        "order_manager": provided["order_manager"],
        "reconciler": provided["reconciler"],
        "report_scheduler": provided["report_scheduler"],
        "settings_service": provided["settings_service"],
        "agent_graph": graph,
    }
    for optional in ("recorder", "session_calendar", "news_calendar"):
        if optional in provided:
            result[optional] = provided[optional]
    return result


__all__ = ["build_runtime_services"]
