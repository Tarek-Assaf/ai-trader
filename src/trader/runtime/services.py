"""Concrete service assembly for the demo runtime.

The API lifespan calls this module after settings and YAML validation.  Every
external dependency is explicit and the function raises before returning a
partial mapping, keeping startup fail closed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from trader.core.audit import SqlAuditWriter
from trader.core.settings import EnvironmentSettings
from trader.runtime.factory import RuntimeDependencyUnavailable

LOGGER = logging.getLogger(__name__)
EXECUTION_LEASE_TTL_MS = 120_000


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
        paper = _dump_mapping(configs.get("paper_execution"))
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

    from trader.data.instruments import InstrumentMapper
    from trader.execution.control import ControlMode
    from trader.execution.journal import ExecutionJournal
    from trader.execution.leader import RedisLeaderLease
    from trader.risk.sizing import SizingLimits
    from trader.runtime.approval import (
        PnlLossGuard,
        ProtectivePositionManager,
        RuntimeApproval,
        RuntimeExecutor,
        RuntimeReconciler,
    )
    from trader.runtime.context import (
        AccountContextSource,
        ContextUnavailable,
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
    soak_monitor = provided.get("soak_monitor")
    if "log_scanner" not in provided:
        from trader.runtime.soak import LogScanner

        workspace = os.getenv("WORKSPACE_DIR", "/workspace")
        sensitive = getattr(settings, "sensitive_values", None)
        patterns = tuple(sensitive()) if callable(sensitive) else ()
        provided["log_scanner"] = LogScanner(workspace, secret_patterns=patterns)
    attach_journal = getattr(soak_monitor, "attach_journal", None)
    if callable(attach_journal):
        attach_journal(journal)
    # The manager ticks every few seconds; a two-minute lease rides out disk and VM stalls.
    execution_lease = RedisLeaderLease(
        redis_client,
        "ai-trader:execution:lease",
        ttl_ms=EXECUTION_LEASE_TTL_MS,
        retries=5,
        retake_when_expired=True,
    )
    guardian_lease = RedisLeaderLease(
        redis_client,
        "ai-trader:guardian:lease",
        ttl_ms=EXECUTION_LEASE_TTL_MS,
        retries=5,
        retake_when_expired=True,
    )

    # Reports are a read-only view over the same objects used by the runtime.
    # Build these providers here, rather than in the LLM/router layer, so a
    # failed model can never prevent an owner command from returning facts.
    if "pnl_provider" not in provided:
        provided["pnl_provider"] = _broker_pnl_provider(broker)
    if "risk_provider" not in provided:
        risk_values = _dump_mapping(configs.get("risk"))

        def risk_provider() -> Mapping[str, object]:
            mode = control_source.read()
            return {
                "mode": getattr(mode, "value", str(mode)),
                "limits": dict(risk_values),
            }

        provided["risk_provider"] = risk_provider

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
        if market_source is None:

            def unavailable_market(_instrument: str) -> Any:
                raise ContextUnavailable("recorder market snapshot is unavailable")

            market_source = unavailable_market
        sizing_source = provided.get("sizing_context_source")
        position_valuation = provided.get("position_valuation")
        if sizing_source is None:
            mapper = provided.get("instrument_mapper")
            if mapper is None:
                market_details_client = provided.get("market_details_client")
                instrument_config = _dump_mapping(configs.get("instruments"))
                if market_details_client is not None and instrument_config:
                    try:
                        mapper = InstrumentMapper(instrument_config, market_details_client)
                    except (TypeError, ValueError) as exc:
                        raise RuntimeDependencyUnavailable(
                            "runtime service unavailable: instrument mapper"
                        ) from exc
            limits = provided.get("sizing_limits")
            if limits is None:
                risk_values = _dump_mapping(configs.get("risk"))
                if {
                    "risk_per_trade_pct",
                    "max_margin_usage_pct",
                    "max_effective_leverage",
                }.issubset(risk_values):
                    try:
                        limits = SizingLimits(
                            Decimal(str(risk_values["risk_per_trade_pct"])),
                            Decimal(str(risk_values["max_margin_usage_pct"])),
                            Decimal(str(risk_values["max_effective_leverage"])),
                        )
                    except (TypeError, ValueError) as exc:
                        raise RuntimeDependencyUnavailable(
                            "runtime service unavailable: sizing limits"
                        ) from exc
            currency = provided.get("account_currency_provider")
            if not callable(currency):

                def currency(_account: Any) -> str:
                    value = broker.get_account().get("currency")
                    if not isinstance(value, str) or not value.strip():
                        raise ContextUnavailable("account currency is unavailable")
                    return value

            if mapper is not None and limits is not None and callable(currency):
                sizing_source = SizingContextSource(
                    mapper.get,
                    limits,
                    account_currency=currency,
                    quote_rate_provider=provided.get("quote_rate_provider"),
                )
            if position_valuation is None and mapper is not None:

                def position_valuation(position: Any) -> Any:
                    from trader.risk.guardian import OpenPosition

                    terms = mapper.get(position.epic)
                    stop = getattr(position, "stop_level", None)
                    level = getattr(position, "level", None)
                    size = getattr(position, "size", None)
                    if str(terms.margin_factor_unit).upper() != "PERCENTAGE":
                        raise ContextUnavailable("margin factor unit is unsupported")
                    if (
                        terms.value_per_point is None
                        or not isinstance(stop, Decimal)
                        or not isinstance(level, Decimal)
                        or not isinstance(size, Decimal)
                        or stop <= 0
                        or level <= 0
                        or size <= 0
                    ):
                        raise ContextUnavailable("position valuation is unavailable")
                    exposure = level * terms.contract_size * size
                    margin = exposure * terms.margin_factor / Decimal("100")
                    risk = abs(level - stop) * terms.value_per_point * size
                    return OpenPosition(position.epic, risk, margin, exposure)

        if sizing_source is None:

            def unavailable_sizing(*_args: Any) -> Any:
                raise ContextUnavailable("instrument terms or sizing limits are unavailable")

            sizing_source = unavailable_sizing
        positions_source = provided.get("positions_provider") or position_source(
            broker, valuation=position_valuation
        )
        if callable(account_source) and callable(market_source) and callable(sizing_source):
            context_provider = RuntimeContextProvider(
                account_source, market_source, sizing_source, positions_source
            )
        else:

            def context_provider(*_args: Any) -> None:
                return None

    pnl_guard: Any = provided.get("pnl_guard")
    position_manager: Any = provided.get("position_manager")
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
            startup_lease_wait_seconds=EXECUTION_LEASE_TTL_MS / 1000 + 10,
        )

    reconciler: Any = provided.get("reconciler") or RuntimeReconciler(
        journal, broker, alert=_log_alert, soak_monitor=soak_monitor
    )
    if "report_email" not in provided:
        from trader.reports.core import SmtpEmailSender

        provided["report_email"] = SmtpEmailSender.from_env()
    report_scheduler: Any = provided.get("report_scheduler") or _ReportScheduler(
        configs.get("schedule"),
        snapshot_provider=provided.get("report_snapshot_provider")
        or _build_report_snapshot_provider(
            broker=broker,
            journal=journal,
            recorder=provided.get("recorder"),
            pnl_provider=provided.get("pnl_provider"),
            risk_provider=provided.get("risk_provider"),
            backup_health_provider=provided.get("backup_health_provider"),
        ),
        telegram=provided.get("report_telegram"),
        email=provided.get("report_email"),
        owner_chat_ids=provided.get("report_owner_chat_ids", ()),
        usage_summary=provided.get("llm_usage_summary")
        or _build_llm_usage_summary(engine, configs),
        state_store=journal,
        recorder=provided.get("recorder"),
        recorder_status_provider=provided.get("recorder_status_provider"),
        version=os.getenv("GIT_SHA", "unknown"),
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
        "guardian_context_provider": context_provider,
        "pnl_guard": pnl_guard,
        "position_manager": position_manager,
    }
    for optional in (
        "recorder",
        "session_calendar",
        "news_calendar",
        "pnl_provider",
        "risk_provider",
        "report_snapshot_provider",
        "report_email",
        "report_telegram",
        "report_owner_chat_ids",
        "llm_usage_summary",
        "soak_monitor",
        "log_scanner",
    ):
        if optional in provided:
            result[optional] = provided[optional]
    return result


def _log_alert(message: str) -> None:
    LOGGER.error("runtime alert: %s", message)


def _dump_mapping(value: object) -> dict[str, Any]:
    method = getattr(value, "model_dump", None)
    raw = method() if callable(method) else value
    return dict(raw) if isinstance(raw, Mapping) else {}


def _broker_pnl_provider(broker: Any) -> Any:
    """Return a fail-closed P&L provider using explicit broker account fields."""

    def provider() -> Any:
        from trader.execution.pnl import BrokerAccountFigures, PnlEngine

        account = broker.get_account()
        if not isinstance(account, Mapping):
            raise RuntimeError("broker account is unavailable")
        names = {
            "balance": ("balance",),
            "equity": ("equity",),
            "currency": ("currency",),
            "margin_used": ("margin_used", "marginUsed"),
            "notional_exposure": ("notional_exposure", "notionalExposure"),
        }
        values: dict[str, object] = {}
        for name, keys in names.items():
            value = next((account[key] for key in keys if key in account), None)
            if value is None:
                raise RuntimeError(f"broker account field unavailable: {name}")
            values[name] = value
        figures = BrokerAccountFigures(
            balance=Decimal(str(values["balance"])),
            equity=Decimal(str(values["equity"])),
            currency=str(values["currency"]),
            margin_used=Decimal(str(values["margin_used"])),
            notional_exposure=Decimal(str(values["notional_exposure"])),
        )
        return PnlEngine.calculate(
            (),
            starting_balance=figures.balance,
            currency=figures.currency,
            broker=figures,
            as_of=datetime.now(UTC),
        )

    return provider


def _build_llm_usage_summary(engine: Any, configs: Mapping[str, object]) -> Any:
    """Build the aggregate SQL usage callback when an LLM budget is configured."""
    llm = _dump_mapping(configs.get("llm"))
    budget = _dump_mapping(llm.get("budget"))
    try:
        daily = Decimal(str(budget["daily_usd_cap"]))
        monthly = Decimal(str(budget["monthly_usd_cap"]))
    except (KeyError, TypeError, ValueError):
        return None
    try:
        from trader.agents.llm.ledger import SqlUsageLedger

        ledger = SqlUsageLedger(engine, daily_cap_usd=daily, monthly_cap_usd=monthly)
    except Exception:
        LOGGER.warning("LLM usage ledger unavailable", extra={"error": "setup"})
        return None
    return ledger.summary


def _health_text(value: object, *, missing: str = "unavailable") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Mapping):
        status = value.get("status")
        if isinstance(status, str) and status.strip():
            return status.strip()
    return missing


def _recorder_health(recorder: Any) -> str:
    if recorder is None:
        return "unavailable"
    getter = getattr(recorder, "health", None) or getattr(recorder, "status", None)
    if callable(getter):
        try:
            return _health_text(getter())
        except Exception:
            return "unavailable"
    path = getattr(recorder, "_status_path", None)
    if path is not None:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            return _health_text(payload)
        except (OSError, ValueError, TypeError):
            return "unavailable"
    return "unavailable"


_RECORDER_HEALTH_FIELDS = (
    "status",
    "ping_reply_ok",
    "market_status",
    "ticks_received",
    "last_tick_at",
    "last_closed_bar_at",
    "reconnect_count",
    "last_reconnect_reason",
    "rejected_candles",
    "last_reject_reason",
    "stale_epics",
)
_SENSITIVE_HEALTH_TEXT = re.compile(
    r"(?:https?://|wss?://|token|api[_-]?key|password|secret|authorization)", re.IGNORECASE
)


def _safe_health_text(value: object, limit: int = 80) -> str:
    """Bound status text and remove values that could contain a credential or URL."""
    text = " ".join(str(value).split())
    if not text or _SENSITIVE_HEALTH_TEXT.search(text):
        return "[REDACTED]" if text else "unknown"
    return text[:limit]


def _recorder_started_at(recorder: Any, fallback: datetime) -> datetime:
    value = getattr(recorder, "_started_at", None)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC)
    return fallback.astimezone(UTC)


def _recorder_health_mapping(
    recorder: Any,
    *,
    status_provider: Callable[[], Mapping[str, object] | None] | None = None,
) -> dict[str, object] | None:
    """Read recorder status through the shared live accessor when supplied."""

    def unavailable() -> dict[str, object]:
        numeric = {"ticks_received", "reconnect_count", "rejected_candles"}
        return {
            name: ([] if name == "stale_epics" else 0)
            if name in numeric | {"stale_epics"}
            else ("unavailable" if name == "status" else None)
            for name in _RECORDER_HEALTH_FIELDS
        } | {"expected_open": None}

    if recorder is None:
        return None
    value: object
    if status_provider is not None:
        # The shared live accessor is authoritative; never fall back to a stale file.
        try:
            value = status_provider()
        except Exception:
            return unavailable()
        if value is None:
            return unavailable()
    else:
        getter = getattr(recorder, "health", None) or getattr(recorder, "status", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                return unavailable()
        else:
            path = getattr(recorder, "_status_path", None)
            if path is None:
                return unavailable()
            try:
                value = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return unavailable()
    if not isinstance(value, Mapping):
        return unavailable()
    output: dict[str, object] = {}
    for name in _RECORDER_HEALTH_FIELDS:
        raw = value.get(name)
        if name == "stale_epics":
            if isinstance(raw, list | tuple):
                output[name] = [
                    _safe_health_text(item, 32) for item in raw[:10] if isinstance(item, str)
                ]
            else:
                output[name] = []
        elif name in {"ticks_received", "reconnect_count", "rejected_candles"}:
            output[name] = max(0, int(raw)) if isinstance(raw, int | float) else 0
        elif name == "ping_reply_ok":
            output[name] = raw if isinstance(raw, bool) else None
        elif raw is None:
            output[name] = None
        else:
            output[name] = _safe_health_text(raw)
    # Needed by the degraded alert only; it is not part of the digest fields.
    expected_open = value.get("expected_open")
    output["expected_open"] = expected_open if isinstance(expected_open, bool) else None
    return output


def _recorder_digest_fields(status: Mapping[str, object]) -> dict[str, object]:
    return {name: status.get(name) for name in _RECORDER_HEALTH_FIELDS}


def _recorder_digest_text(payload: Mapping[str, object]) -> str:
    fields = payload.get("data_recorder")
    values = fields if isinstance(fields, Mapping) else {}
    lines = [
        "Recorder health digest",
        f"version: {_safe_health_text(payload.get('version'))}",
        "window UTC: "
        f"{_safe_health_text(payload.get('window_start'), 40)} -> "
        f"{_safe_health_text(payload.get('window_end'), 40)}",
    ]
    for name in _RECORDER_HEALTH_FIELDS:
        value = values.get(name)
        if name == "stale_epics" and isinstance(value, list):
            rendered = ",".join(value) or "none"
        else:
            rendered = "none" if value is None else _safe_health_text(value, 80)
        lines.append(f"{name}: {rendered}")
    return "\n".join(lines)[:1800]


def _recorder_health_schedule(schedule: object) -> dict[str, Any]:
    """Read recorder digest settings while preserving safe bounded defaults."""
    raw = getattr(schedule, "model_dump", lambda: schedule)()
    config = (
        raw.get("recorder_health", raw.get("recorder_health_digest", {}))
        if isinstance(raw, Mapping)
        else {}
    )
    if not isinstance(config, Mapping):
        config = {}
    values = config.get("digest_hours", (2, 24))
    hours: list[float] = []
    if isinstance(values, list | tuple):
        for item in values:
            try:
                number = float(item)
            except (TypeError, ValueError):
                continue
            if number > 0 and number <= 24 * 365:
                hours.append(number)
    if not hours:
        hours = [2.0, 24.0]
    hours = sorted(set(hours))
    try:
        repeat_hours = float(config.get("repeat_hours", 24))
    except (TypeError, ValueError):
        repeat_hours = 24.0
    try:
        degraded_minutes = float(config.get("degraded_minutes", 5))
    except (TypeError, ValueError):
        degraded_minutes = 5.0
    return {
        "digest_hours": hours,
        "repeat_hours": max(1.0, min(repeat_hours, 24 * 365)),
        "degraded_minutes": max(1.0, min(degraded_minutes, 24 * 60)),
    }


def _health_alert_limiter() -> Any:
    from trader.channels.telegram import AlertLimiter

    return AlertLimiter(interval_seconds=300.0)


def _build_report_snapshot_provider(
    *,
    broker: Any,
    journal: Any,
    recorder: Any,
    pnl_provider: Any,
    risk_provider: Any,
    backup_health_provider: Any,
) -> Any:
    """Compose a report snapshot without manufacturing missing facts."""
    from trader.reports.core import ReportSnapshot

    def provider(period: str) -> ReportSnapshot:
        pnl = pnl_provider() if callable(pnl_provider) else None
        if pnl is None:
            raise RuntimeError("P&L provider is unavailable")
        now = datetime.now(UTC)
        usage_risk = risk_provider() if callable(risk_provider) else {}
        risk_events: tuple[str, ...] = ()
        if isinstance(usage_risk, Mapping):
            mode = usage_risk.get("mode")
            if isinstance(mode, str) and mode not in {"run", "RUN"}:
                risk_events = (f"control mode: {mode}",)
        if callable(backup_health_provider):
            try:
                backup = _health_text(backup_health_provider())
            except Exception:
                backup = "unavailable"
        else:
            backup = _backup_health()
        r_values = tuple(getattr(pnl, "r_multiples", ()))
        wins = sum(1 for value in r_values if getattr(value, "net_profit", Decimal(0)) > 0)
        expectancy = (
            sum((value.net_profit for value in r_values), Decimal(0)) / Decimal(len(r_values))
            if r_values
            else Decimal(0)
        )
        issues: tuple[str, ...] = ()
        reconciliation = getattr(pnl, "reconciliation", None)
        if getattr(reconciliation, "alert_required", False):
            issues = ("P&L reconciliation is outside tolerance",)
        return ReportSnapshot(
            period=period,  # type: ignore[arg-type]
            generated_at=now,
            pnl=pnl,
            trade_count=len(r_values),
            winning_trades=wins,
            expectancy=expectancy,
            risk_events=risk_events,
            data_health=_recorder_health(recorder),
            backup_health=backup,
            issues=issues,
        )

    return provider


def _backup_health() -> str:
    """Return a filesystem backup status without exposing paths or filenames."""
    directory = Path(os.getenv("BACKUP_DIR", "/workspace/backups"))
    try:
        fresh = any(
            path.is_file() and path.stat().st_size > 0 for path in directory.glob("trader-*.dump")
        )
    except OSError:
        return "unavailable"
    return "fresh" if fresh else "stale"


class _ReportScheduler:
    def __init__(
        self,
        schedule: object,
        *,
        snapshot_provider: Any = None,
        telegram: Any = None,
        email: Any = None,
        owner_chat_ids: object = (),
        usage_summary: Any = None,
        state_store: Any = None,
        recorder: Any = None,
        recorder_status_provider: Callable[[], Mapping[str, object] | None] | None = None,
        version: str | None = None,
        evidence_path: str | Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        alert: Callable[[Any], bool] | None = None,
    ) -> None:
        self.schedule = schedule
        self._state_store = state_store
        self._recorder = recorder
        self._recorder_status_provider = recorder_status_provider
        self._clock = clock
        self._version = _safe_health_text(version or os.getenv("GIT_SHA", "unknown"), 80)
        workspace = Path(os.getenv("WORKSPACE_DIR", "/workspace"))
        self._evidence_path = Path(
            evidence_path or workspace / "state" / "evidence" / "recorder_health.jsonl"
        )
        self._alert_sender = alert
        self._alert_limiter = _health_alert_limiter()
        self._digest_sent: set[str] = set()
        self._degraded_since: datetime | None = None
        self._degraded_alert_sent = False
        self._recorder_started_at = _recorder_started_at(recorder, self._clock())
        from trader.reports.commands import ScheduledReportRunner
        from trader.reports.core import ReportPeriod, ReportSnapshot

        def unavailable(_period: ReportPeriod) -> ReportSnapshot:
            raise RuntimeError("report snapshot provider is unavailable")

        provider = snapshot_provider if callable(snapshot_provider) else unavailable
        ids = tuple(owner_chat_ids) if isinstance(owner_chat_ids, tuple | list) else ()
        self._runner = ScheduledReportRunner(
            schedule,
            provider,
            telegram=telegram,
            email=email,
            owner_chat_ids=ids,
            usage_summary=usage_summary if callable(usage_summary) else None,
        )
        self._last_run = self._load_last_run()

    def run(self, period: str) -> Any:
        if period not in {"daily", "weekly", "monthly"}:
            raise ValueError("unsupported report period")
        return self._runner.run(period)  # type: ignore[arg-type]

    def render(self, period: str) -> Any:
        if period not in {"daily", "weekly", "monthly"}:
            raise ValueError("unsupported report period")
        return self._runner.render(period)  # type: ignore[arg-type]

    def configure_delivery(self, *, telegram: Any, email: Any, owner_chat_ids: object) -> None:
        self._runner.configure_delivery(
            telegram=telegram,
            email=email,
            owner_chat_ids=owner_chat_ids if isinstance(owner_chat_ids, tuple | list) else (),
        )

    def configure_alert(self, alert: Callable[[Any], bool] | None) -> None:
        """Attach the shared Telegram channel alert path after startup wiring."""
        self._alert_sender = alert

    def startup_schedule_line(self, now: datetime | None = None) -> str:
        """Return a bounded, secret-free description of the report schedule."""
        current = (now or self._clock()).astimezone(UTC)
        targets = self._targets(current)
        periods = "/".join(period for period, _target in targets) or "none"
        future = [target for _period, target in targets if target >= current]
        next_target = min(future or [target for _period, target in targets], default=None)
        next_text = next_target.isoformat() if next_target is not None else "none"
        return f"reports scheduled {periods} next={next_text}"

    def run_due(self, now: datetime | None = None) -> tuple[str, ...]:
        """Run each configured period once when its UTC schedule is due."""
        current = (now or self._clock()).astimezone(UTC)
        ran: list[str] = []
        for period, target in self._targets(current):
            key = target.isoformat()
            if self._last_run.get(period) == key or current < target:
                continue
            if self.run(period) is None:
                # Snapshot failed (already logged); retry on the next cycle
                # instead of marking the period as delivered.
                continue
            self._last_run[period] = key
            self._save_last_run()
            ran.append(period)
        ran.extend(self._run_recorder_health(current))
        return tuple(ran)

    def _run_recorder_health(self, now: datetime) -> list[str]:
        """Emit configured recorder digests and liveness alerts."""
        status = _recorder_health_mapping(
            self._recorder,
            status_provider=self._recorder_status_provider,
        )
        if status is None:
            return []
        self._update_recorder_alert(status, now)
        uptime = max(0.0, (now - self._recorder_started_at).total_seconds())
        digest_config = _recorder_health_schedule(self.schedule)
        thresholds = digest_config["digest_hours"]
        repeat_seconds = digest_config["repeat_hours"] * 3600.0
        labels: list[str] = []
        for threshold in thresholds:
            label = f"{threshold:g}h"
            if uptime >= threshold * 3600.0 and label not in self._digest_sent:
                self._emit_recorder_digest(status, now, label)
                self._digest_sent.add(label)
                labels.append(f"recorder_health_{label}")
        if thresholds and uptime >= thresholds[-1] * 3600.0:
            last_threshold = thresholds[-1] * 3600.0
            cycles = int((uptime - last_threshold) // repeat_seconds) if repeat_seconds else 0
            for cycle in range(1, cycles + 1):
                label = f"{thresholds[-1] + cycle * digest_config['repeat_hours']:g}h"
                if label in self._digest_sent:
                    continue
                self._emit_recorder_digest(status, now, label)
                self._digest_sent.add(label)
                labels.append(f"recorder_health_{label}")
        return labels

    def _update_recorder_alert(self, status: Mapping[str, object], now: datetime) -> None:
        """Alert once after a sustained open-market degradation and on recovery."""
        config = _recorder_health_schedule(self.schedule)
        degraded = status.get("status") == "degraded" and status.get("expected_open") is True
        if degraded:
            self._degraded_since = self._degraded_since or now
            if (
                not self._degraded_alert_sent
                and (now - self._degraded_since).total_seconds() >= config["degraded_minutes"] * 60
            ):
                self._send_health_alert(
                    "recorder_health_degraded",
                    "Recorder degraded while the market is expected open.",
                )
                self._degraded_alert_sent = True
            return
        if self._degraded_alert_sent and status.get("status") == "healthy":
            self._send_health_alert("recorder_health_recovered", "Recorder health recovered.")
            self._degraded_alert_sent = False
        self._degraded_since = None

    def _send_health_alert(self, key: str, text: str) -> None:
        """Deliver through the shared Alert/AlertLimiter path when configured."""
        from trader.channels.telegram import Alert

        alert = Alert(key, text[:240])
        if not self._alert_limiter.allow(key):
            return
        sender = self._alert_sender
        if callable(sender):
            try:
                sender(alert)
            except Exception:
                LOGGER.warning("recorder health alert delivery failed", extra={"error": "send"})
            return
        transport = self._runner._telegram
        if transport is None:
            return
        for chat_id in self._runner._owner_chat_ids:
            try:
                transport.send_message(chat_id, alert.text)
            except Exception:
                LOGGER.warning("recorder health alert delivery failed", extra={"error": "send"})

    def _emit_recorder_digest(
        self, status: Mapping[str, object], now: datetime, label: str
    ) -> None:
        """Write and deliver one bounded, secret-free recorder health digest."""
        start = self._recorder_started_at
        fields = _recorder_digest_fields(status)
        payload = {
            "version": self._version,
            "window_start": start.isoformat(),
            "window_end": now.isoformat(),
            "uptime_label": label,
            "data_recorder": fields,
        }
        text = _recorder_digest_text(payload)
        payload["message"] = text
        try:
            self._evidence_path.parent.mkdir(parents=True, exist_ok=True)
            with self._evidence_path.open("a", encoding="utf-8") as evidence:
                evidence.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        except OSError:
            LOGGER.warning("recorder health evidence unavailable", extra={"error": "write"})
        self._send_health_alert(f"recorder_health_digest_{label}", text)

    def _targets(self, now: datetime) -> tuple[tuple[str, datetime], ...]:
        raw = getattr(self.schedule, "model_dump", lambda: self.schedule)()
        reports = raw.get("reports", {}) if isinstance(raw, Mapping) else {}
        if not isinstance(reports, Mapping):
            return ()
        output: list[tuple[str, datetime]] = []
        for period in ("daily", "weekly", "monthly"):
            config = reports.get(period)
            if not isinstance(config, Mapping):
                continue
            at = config.get("at")
            if not isinstance(at, str) or len(at) != 5 or at[2] != ":":
                continue
            try:
                hour, minute = (int(value) for value in at.split(":"))
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            except (TypeError, ValueError):
                continue
            if period == "weekly":
                day = str(config.get("day", "")).lower()
                weekdays = {
                    name: index
                    for index, name in enumerate(
                        (
                            "monday",
                            "tuesday",
                            "wednesday",
                            "thursday",
                            "friday",
                            "saturday",
                            "sunday",
                        )
                    )
                }
                if day not in weekdays:
                    continue
                target += timedelta(days=(weekdays[day] - target.weekday()) % 7)
            elif period == "monthly":
                try:
                    day_of_month = int(config.get("day_of_month") or 0)
                    target = target.replace(day=day_of_month)
                except (TypeError, ValueError):
                    continue
            output.append((period, target))
        return tuple(output)

    def _load_last_run(self) -> dict[str, str]:
        getter = getattr(self._state_store, "get_snapshot", None)
        if not callable(getter):
            return {}
        try:
            value = getter("reports:last-run")
            return dict(value) if isinstance(value, Mapping) else {}
        except Exception:
            LOGGER.warning("report schedule state unavailable", extra={"error": "read"})
            return {}

    def _save_last_run(self) -> None:
        saver = getattr(self._state_store, "save_snapshot", None)
        if not callable(saver):
            return
        try:
            saver("reports:last-run", self._last_run)
        except Exception:
            LOGGER.warning("report schedule state unavailable", extra={"error": "write"})


__all__ = ["build_runtime_services"]
