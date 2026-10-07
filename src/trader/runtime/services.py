"""Concrete service assembly for the demo runtime.

The API lifespan calls this module after settings and YAML validation.  Every
external dependency is explicit and the function raises before returning a
partial mapping, keeping startup fail closed.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
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
    attach_journal = getattr(soak_monitor, "attach_journal", None)
    if callable(attach_journal):
        attach_journal(journal)
    execution_lease = RedisLeaderLease(redis_client, "ai-trader:execution:lease")
    guardian_lease = RedisLeaderLease(redis_client, "ai-trader:guardian:lease")

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


def _dump_mapping(value: object) -> dict[str, object]:
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
    ) -> None:
        self.schedule = schedule
        self._state_store = state_store
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

    def run_due(self, now: datetime | None = None) -> tuple[str, ...]:
        """Run each configured period once when its UTC schedule is due."""
        current = (now or datetime.now(UTC)).astimezone(UTC)
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
        return tuple(ran)

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
