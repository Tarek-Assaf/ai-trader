"""FastAPI application for health and operational status."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import random
import threading
import traceback
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI

from trader.api.control import router as control_router
from trader.api.health import health_payload, set_runtime_health
from trader.channels.router import ChatRouter, RoutedIntent
from trader.channels.telegram import TelegramBotTransport, TelegramChannel
from trader.core.audit import AuditEvent
from trader.core.config import load_all_configs
from trader.core.logging import configure_logging
from trader.core.settings import EnvironmentSettings
from trader.data.instruments import InstrumentMapper
from trader.data.recorder import CapitalDataRecorder, ParquetCandleStore
from trader.execution.control import ControlMode, build_control_sources
from trader.execution.reconciliation import capital_account_field_map_from_config
from trader.integrations.capital_com.client import CapitalComClient
from trader.lab.runner import LabJobManager, LabRunner
from trader.reports.commands import CommandDataService
from trader.runtime import (
    RuntimeComponents,
    RuntimeFeedAdapters,
    ScheduleCycleSource,
    build_default_factories,
    build_runtime,
    build_runtime_services,
    runtime_enabled,
)
from trader.runtime.factory import (
    RuntimeDependencyUnavailable,
    RuntimeFactories,
    build_runtime_components,
)
from trader.soak import SoakMonitor

_LOG = logging.getLogger(__name__)


def _runtime_failure_detail(exc: BaseException) -> str:
    """Return a short, secret-free diagnostic for a runtime startup failure.

    Dependency failures have fixed, secret-free messages that are useful to an
    operator. Arbitrary exceptions expose only their type and the innermost
    source location; exception values and messages must never enter health.
    """

    frames = traceback.extract_tb(exc.__traceback__)
    location = "unknown:unknown"
    if frames:
        frame = frames[-1]
        location = f"{Path(frame.filename).name}:{frame.name}"
    prefix = type(exc).__name__
    if isinstance(exc, RuntimeDependencyUnavailable):
        message = str(exc).strip()
        if message:
            return f"{prefix}: {message} ({location})"[:200]
    return f"{prefix} ({location})"[:200]


def _report_telegram_transport(state: Any, token: str) -> TelegramBotTransport:
    """Return the configured report transport, falling back safely.

    Runtime service construction can fail while the broker is rate limited.  In
    that path the optional transport is never assigned, but Telegram channel
    startup still needs a transport when the owner has configured Telegram.
    ``getattr`` keeps the degraded startup path alive without exposing the
    broker exception or any credentials.
    """
    configured = getattr(state, "report_telegram_transport", None)
    return configured or TelegramBotTransport(token)


def _command_renderer(command_handlers: dict[str, Any]) -> Callable[[str, str], str]:
    """Render read-only Telegram commands through their data-service handlers."""

    def render(command: str, argument: str) -> str:
        unavailable = f"{command[:32]} is unavailable right now."
        handler = command_handlers.get(command)
        if not callable(handler):
            return unavailable
        full_command = f"{command} {argument}".strip()
        try:
            value = handler(full_command)
        except Exception as exc:  # noqa: BLE001 - command failures stay bounded
            _LOG.warning(
                "telegram command handler failed command=%s (%s)",
                command[:32],
                type(exc).__name__,
            )
            return unavailable
        return value if isinstance(value, str) else unavailable

    return render


def _start_runtime_if_configured(
    app: FastAPI,
    settings: EnvironmentSettings,
    configs: dict[str, Any],
) -> None:
    """Start the core runtime only after an explicit complete composition.

    The application owns environment/config validation, while deployment code
    supplies ``app.state.runtime_components_factory`` once Redis, the database,
    PaperBroker, Guardian, OrderManager, reconciler, scheduler and settings
    service are constructed.  Refusing to invent partial components is a
    deliberate fail-safe: an eligible pod with missing dependencies stays
    stopped and keeps pause/kill controls available.
    """
    if not app.state.runtime_eligible:
        return
    control_source = app.state.execution_control_source
    factory = getattr(app.state, "runtime_components_factory", None)
    factories = getattr(app.state, "runtime_factories", None)
    if factory is None and not isinstance(factories, RuntimeFactories):
        try:
            factories = build_default_factories(
                settings,
                configs,
                services=getattr(app.state, "runtime_services", None),
            )
            app.state.runtime_factories = factories
        except Exception as exc:
            set_runtime_health("runtime_unavailable", _runtime_failure_detail(exc))
            _LOG.exception("runtime default composition unavailable")
    if factory is None and isinstance(factories, RuntimeFactories):

        def factory(current_settings: EnvironmentSettings, current_configs: dict[str, Any]) -> Any:
            return build_runtime_components(
                current_settings,
                current_configs,
                control_source,
                factories,
            )

    if not callable(factory) or control_source is None:
        _LOG.warning("runtime dependencies unavailable; core runtime remains stopped")
        return
    try:
        components = factory(settings, configs)
        if not isinstance(components, RuntimeComponents):
            raise TypeError("runtime component factory returned an invalid composition")
        if components.soak_monitor is None:
            components.soak_monitor = getattr(app.state, "soak_monitor", None)
        services = getattr(app.state, "runtime_services", None) or {}
        for name in ("pnl_guard", "position_manager", "log_scanner"):
            if getattr(components, name, None) is None and isinstance(services, dict):
                setattr(components, name, services.get(name))
        missing = components.missing_required()
        if missing:
            raise RuntimeError(f"runtime composition is incomplete: {', '.join(missing)}")
        schedule = configs.get("schedule")
        dump = getattr(schedule, "model_dump", None)
        schedule_values = dump() if callable(dump) else schedule
        cycle_source = (
            ScheduleCycleSource.from_config(schedule_values)
            if isinstance(schedule_values, dict)
            else None
        )
        if cycle_source is not None:
            # Recorder, session-calendar and news workers share this exact
            # source; adapters only enqueue stable trigger IDs and never trade.
            app.state.runtime_cycle_source = cycle_source
            app.state.runtime_feed_adapters = RuntimeFeedAdapters(
                cycle_source,
                alert=lambda message: _LOG.error("runtime feed alert: %s", message),
            )
            for source_name in ("recorder", "session_calendar", "news_calendar"):
                source = getattr(app.state, source_name, None)
                register = getattr(source, "register_feed_adapters", None)
                if callable(register):
                    register(app.state.runtime_feed_adapters)
        tai_runtime = _build_tai_demo_runtime(
            app,
            settings,
            configs,
            components,
            cycle_source,
            capital_client=getattr(app.state, "capital_client", None),
        )
        if tai_runtime is not None:
            metadata = dict(components.metadata)
            metadata["tai_demo_runtime"] = tai_runtime
            components.metadata = metadata
        runtime = build_runtime(
            components=components,
            control_source=control_source,
            alert=lambda message: _LOG.error("runtime alert: %s", message),
            cycle_source=cycle_source,
        )
        app.state.runtime = runtime
        # Lifespan is async, so the caller starts the service immediately after
        # this composition hook returns.
    except Exception as exc:
        app.state.runtime = None
        set_runtime_health("runtime_unavailable", _runtime_failure_detail(exc))
        _LOG.exception("runtime composition unavailable")


def _build_tai_demo_runtime(
    app: FastAPI,
    settings: EnvironmentSettings,
    configs: dict[str, Any],
    components: RuntimeComponents,
    cycle_source: Any,
    *,
    capital_client: Any,
) -> Any:
    """Attach the opt-in Tai runner to the shared guarded cycle runtime."""
    app.state.tai_feed_tailer = None
    if not bool(getattr(settings, "tai_demo_experiment", False)):
        if not getattr(app.state, "tai_startup_logged", False):
            _LOG.info(
                "Tai demo startup experiment=off dry_run=%s epic=disabled warmup_bars=0 "
                "start_balance_present=%s",
                "on" if bool(getattr(settings, "tai_demo_dry_run", True)) else "off",
                "yes" if bool(str(getattr(settings, "tai_demo_start_balance", ""))) else "no",
            )
            app.state.tai_startup_logged = True
        return None
    if cycle_source is None or capital_client is None:
        raise RuntimeDependencyUnavailable(
            "Tai demo wiring requires cycle source and Capital client"
        )
    from trader.experiments.tai_feed import TaiLakeTailer
    from trader.experiments.tai_wiring import (
        CapitalBalanceProvider,
        CapitalInstrumentRulesProvider,
        TaiSignalProvider,
        ThreeMinuteBarBuilder,
        build_tai_demo_runtime,
    )

    raw = configs.get("instruments")
    dump = getattr(raw, "model_dump", None)
    values = dump() if callable(dump) else raw
    instruments = values.get("instruments", values) if isinstance(values, dict) else {}
    epic = next(
        (
            str(item.get("capital_epic"))
            for item in instruments.values()
            if isinstance(item, dict) and item.get("capital_epic")
        ),
        "US100",
    )
    workspace = Path(os.getenv("WORKSPACE_DIR", "/workspace"))
    lake_root = Path(os.getenv("DATA_LAKE_DIR", str(workspace / "data" / "lake")))
    signal = TaiSignalProvider.from_lake(lake_root, epic)
    journal_object = (getattr(app.state, "runtime_services", None) or {}).get("journal")
    append_audit = getattr(journal_object, "append", None)

    def journal(payload: Mapping[str, object]) -> None:
        if callable(append_audit):
            event_type = str(payload.get("event", "tai_demo"))
            append_audit(AuditEvent(event_type, dict(payload)))

    def alert(message: str) -> None:
        _LOG.error("tai demo alert: %s", message)
        channel = getattr(app.state, "telegram_channel", None)
        send_alert = getattr(channel, "send_alert", None)
        if callable(send_alert):
            from trader.channels.telegram import Alert

            send_alert(Alert("tai_demo", message[:240]))

    runtime_adapter = build_tai_demo_runtime(
        settings,
        epic=epic,
        order_manager=cast(Any, components.order_manager),
        position_gateway=cast(Any, components.broker),
        control=app.state.execution_control_source,
        source=cycle_source,
        builder=ThreeMinuteBarBuilder(epic),
        balance_provider=CapitalBalanceProvider(capital_client),
        rules_provider=CapitalInstrumentRulesProvider(capital_client, epic),
        signal_provider=signal,
        journal=journal if callable(append_audit) else None,
        alert=alert,
    )
    if runtime_adapter is not None:
        history = getattr(signal, "history", ())
        seed_after = history[-1].at + timedelta(minutes=2) if history else None
        app.state.tai_feed_tailer = TaiLakeTailer(
            lake_root,
            epic,
            runtime_adapter,
            seed_after=seed_after,
        )
        if not getattr(app.state, "tai_startup_logged", False):
            _LOG.info(
                "Tai demo startup experiment=on dry_run=%s epic=%s warmup_bars=%d "
                "start_balance_present=%s",
                "on" if bool(getattr(settings, "tai_demo_dry_run", True)) else "off",
                epic,
                getattr(signal, "warmup_bars", 0),
                "yes" if bool(str(getattr(settings, "tai_demo_start_balance", ""))) else "no",
            )
            app.state.tai_startup_logged = True
    return runtime_adapter


async def _tai_feed_loop(app: FastAPI) -> None:
    """Tail the recorder lake without coupling the API to the worker stream."""
    while True:
        tailer = getattr(app.state, "tai_feed_tailer", None)
        if tailer is not None:
            try:
                tailer.poll_once()
            except Exception:  # noqa: BLE001 - a lake read must not stop the API
                _LOG.warning("Tai lake feed poll failed", exc_info=True)
            await asyncio.sleep(tailer.interval_seconds)
        else:
            await asyncio.sleep(1.0)


def _start_tai_feed_tailer(app: FastAPI) -> None:
    """Start one API-process tailer only when the gated runtime is composed."""
    tailer = getattr(app.state, "tai_feed_tailer", None)
    task = getattr(app.state, "tai_feed_task", None)
    if tailer is not None and (task is None or task.done()):
        app.state.tai_feed_task = asyncio.create_task(
            _tai_feed_loop(app), name="tai-lake-feed"
        )


class _MemoryUsageLedger:
    """Small process-local ledger used only when no database engine is available."""

    def __init__(self, *, daily_cap: Decimal, monthly_cap: Decimal) -> None:
        self.daily_cap = daily_cap
        self.monthly_cap = monthly_cap
        self.reserved = Decimal(0)
        self.records: list[dict[str, object]] = []

    def reserve(self, *, role: str, model: str, maximum_cost: Decimal) -> str:
        from uuid import uuid4

        maximum = Decimal(str(maximum_cost))
        if not role or not model or maximum <= 0 or not maximum.is_finite():
            raise ValueError("invalid LLM reservation")
        if self.reserved + maximum > self.monthly_cap or self.reserved + maximum > self.daily_cap:
            raise RuntimeError("configured LLM spend cap reached")
        reservation = str(uuid4())
        self.reserved += maximum
        self.records.append({"id": reservation, "role": role, "model": model, "reserved": maximum})
        return reservation

    def settle(
        self,
        reservation_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal,
        latency_ms: int,
        outcome: str,
    ) -> None:
        for record in self.records:
            if record["id"] == reservation_id:
                record.update(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    outcome=outcome,
                )
                return
        raise RuntimeError("LLM reservation was not found")


class _MockIntentClient:
    """Deterministic no-network LLM path used by default on the pod."""

    async def complete(self, role: str, messages: tuple[Any, ...], schema: type[Any]) -> Any:
        del role, messages, schema
        return RoutedIntent(
            intent="unknown",
            confidence=0.0,
            clarifying_question="The mock assistant is enabled; use a supported command.",
            flags=("mock",),
        )


def _chart_png(_command: str, argument: str) -> bytes:
    """Render an empty, headless chart without querying a broker or database."""
    from trader.viz import equity_curve

    figure = equity_curve([], title=argument or "Trading chart")
    buffer = io.BytesIO()
    try:
        figure.savefig(buffer, format="png", dpi=120)
        return buffer.getvalue()
    finally:
        clear = getattr(figure, "clear", None)
        if callable(clear):
            clear()


def _build_chat_router(
    settings: EnvironmentSettings,
    configs: dict[str, Any],
    *,
    control_handler: Any,
    command_handlers: dict[str, Any],
) -> ChatRouter:
    """Build a mock-by-default router; hosted LLM use is opt-in and budgeted."""
    provider_name = (settings.llm_provider or os.getenv("LLM_PROVIDER") or "mock").strip().lower()
    llm: Any = _MockIntentClient()
    if provider_name == "anthropic" and settings.anthropic_api_key is not None:
        try:
            from trader.agents.llm.client import LLMClient

            llm_config = configs["llm"].model_dump()
            budget = llm_config["budget"]
            monthly_cap = Decimal(str(budget["monthly_usd_cap"]))
            daily_cap = Decimal(str(budget["daily_usd_cap"]))
            if monthly_cap <= 0 or daily_cap <= 0:
                raise ValueError("LLM monthly cap must be positive")
            llm = LLMClient.from_environment(
                config=llm_config,
                ledger=_MemoryUsageLedger(daily_cap=daily_cap, monthly_cap=monthly_cap),
            )
        except Exception as exc:
            _LOG.warning(
                "Anthropic router unavailable (%s); using mock provider", type(exc).__name__
            )
    return ChatRouter(
        llm=llm,
        control_handler=control_handler,
        command_handlers=command_handlers,
    )


def _install_runtime_services(
    app: FastAPI,
    settings: EnvironmentSettings,
    configs: dict[str, Any],
    *,
    recorder: CapitalDataRecorder | None,
    capital_client: CapitalComClient | None,
) -> None:
    """Build and register the complete runtime service mapping."""
    app.state.runtime_services = build_runtime_services(
        settings,
        configs,
        overrides={
            "control_source": app.state.execution_control_source,
            "control_writer": app.state.execution_control_writer,
            "recorder": recorder,
            "instrument_mapper": app.state.instrument_mapper,
            "market_details_client": capital_client,
            "soak_monitor": app.state.soak_monitor,
            **(getattr(app.state, "runtime_service_overrides", None) or {}),
        },
    )
    for source_name in ("recorder", "session_calendar", "news_calendar"):
        setattr(app.state, source_name, app.state.runtime_services.get(source_name))
    services: Any = app.state.runtime_services
    scheduler = services.get("report_scheduler")

    def report_provider(period: str) -> Any:
        render = getattr(scheduler, "render", None)
        if not callable(render):
            render = getattr(scheduler, "run", None)
        if not callable(render):
            raise RuntimeError("report scheduler is unavailable")
        return render(period)

    app.state.command_data_service = CommandDataService(
        services.get("broker"),
        pnl_provider=services.get("pnl_provider"),
        risk_provider=services.get("risk_provider"),
        report_provider=report_provider,
        soak_provider=lambda: app.state.soak_monitor,
    )


async def _runtime_retry_loop(
    app: FastAPI,
    settings: EnvironmentSettings,
    configs: dict[str, Any],
    *,
    recorder: CapitalDataRecorder | None,
    capital_client: CapitalComClient | None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[float, float], float] = random.uniform,
) -> None:
    """Retry runtime composition in the background with bounded backoff."""
    attempt = 0
    while True:
        base_delay = min(30.0 * (2**attempt), 300.0)
        delay = base_delay + jitter(0.0, min(30.0, base_delay * 0.1))
        await sleep(delay)
        try:
            _install_runtime_services(
                app,
                settings,
                configs,
                recorder=recorder,
                capital_client=capital_client,
            )
            _start_runtime_if_configured(app, settings, configs)
            runtime = getattr(app.state, "runtime", None)
            if runtime is None:
                raise RuntimeError("runtime composition unavailable")
            await runtime.start()
            _start_tai_feed_tailer(app)
            set_runtime_health(None)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep API alive while retrying
            detail = _runtime_failure_detail(exc)
            set_runtime_health("runtime_unavailable", detail)
            _LOG.exception("runtime retry unavailable")
            attempt = min(attempt + 1, 4)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    """Fail startup on invalid settings or config; register secret redaction."""
    settings = EnvironmentSettings()
    configure_logging(settings.sensitive_values())
    redis_url = settings.redis_url.get_secret_value() if settings.redis_url is not None else None
    if settings.role == "all":
        redis_url = redis_url or "redis://127.0.0.1:6379/0"
    _app.state.execution_control_source = None
    _app.state.execution_control_writer = None
    _app.state.telegram_channel = None
    _app.state.telegram_stop = None
    _app.state.telegram_thread = None
    _app.state.runtime = None
    _app.state.runtime_cycle_source = None
    _app.state.runtime_feed_adapters = None
    _app.state.tai_feed_tailer = None
    _app.state.tai_feed_task = None
    _app.state.tai_startup_logged = False
    _app.state.runtime_services = None
    _app.state.runtime_service_overrides = None
    set_runtime_health(None)
    # Runtime service assembly is best effort.  Always create this state slot
    # before the optional report scheduler wiring so a broker 429 cannot turn
    # into an AttributeError while the Telegram channel starts.
    _app.state.report_telegram_transport = None
    _app.state.command_data_service = None
    _app.state.lab_job_manager = None
    _app.state.soak_monitor = SoakMonitor(
        Path(os.getenv("SOAK_EVIDENCE_DIR", "/workspace/state/soak"))
    )
    _app.state.runtime_eligible = runtime_enabled(settings)
    if _app.state.runtime_eligible:
        # Runtime dependencies are composed only after the validated broker,
        # database and Redis services are available.  Keeping this explicit
        # state prevents a paper/demo startup from silently running a partial
        # decision loop that could bypass OrderManager.
        _LOG.info("Demo runtime composition is eligible; waiting for core dependencies")
    if redis_url:
        try:
            control_source, control_writer = build_control_sources(redis_url=redis_url)
            _app.state.execution_control_source = control_source
            _app.state.execution_control_writer = control_writer
        except Exception as exc:
            _LOG.warning("Execution control unavailable (%s)", type(exc).__name__)
    default_dir = Path("/app/config") if Path("/app/config").is_dir() else Path("config")
    baked_ceilings = Path("/app/baked/risk_ceilings.yaml")
    if not baked_ceilings.is_file():
        baked_ceilings = default_dir / "risk_ceilings.yaml"
    volume_dir = Path("/workspace/config")
    configs = load_all_configs(
        default_dir,
        volume_config_dir=volume_dir if volume_dir.is_dir() else None,
        baked_ceilings_path=baked_ceilings,
    )
    _app.state.instrument_mapper = None
    _app.state.capital_account_fields = None
    _app.state.capital_client = None
    capital_client: CapitalComClient | None = None
    has_capital_credentials = all(
        value is not None and bool(value.get_secret_value())
        for value in (
            settings.capital_api_key,
            settings.capital_identifier,
            settings.capital_api_password,
        )
    )
    # Without broker credentials the runtime is intentionally not composed, so
    # that is not a degraded state and there is nothing to retry.
    runtime_expected = bool(_app.state.runtime_eligible and has_capital_credentials)
    if settings.role in {"all", "core"} and has_capital_credentials:
        try:
            _app.state.capital_account_fields = capital_account_field_map_from_config(
                configs["settings"].model_dump()
            )
        except ValueError as exc:
            _LOG.error(
                "Capital.com account mapping not verified; reconciliation disabled (%s)", exc
            )
        try:
            capital_client = CapitalComClient(settings)
            _app.state.capital_client = capital_client
            mapper = InstrumentMapper(configs["instruments"].model_dump(), capital_client)
            _app.state.instrument_mapper = mapper
            mapper.refresh_all()
        except Exception as exc:
            _app.state.instrument_mapper = None
            _LOG.warning("Capital.com instrument metadata unavailable (%s)", type(exc).__name__)
            if capital_client is not None:
                capital_client.close()
                capital_client = None
                _app.state.capital_client = None
    recorder: CapitalDataRecorder | None = None
    try:
        raw_instruments = configs["instruments"].model_dump()["instruments"]
        symbols_by_epic = {
            str(values["capital_epic"]): str(symbol)
            for symbol, values in raw_instruments.items()
            if isinstance(values, dict) and values.get("capital_epic")
        }
        workspace = Path(os.getenv("WORKSPACE_DIR", "/workspace"))
        lake_root = Path(os.getenv("DATA_LAKE_DIR", str(workspace / "data" / "lake")))
        holdout_root = Path(os.getenv("LAB_HOLDOUT_DIR", str(workspace / "data" / "holdout")))
        _app.state.lab_job_manager = LabJobManager(
            LabRunner(lake_root=lake_root, holdout_root=holdout_root)
        )
        status_path = Path(
            os.getenv("RECORDER_STATUS_PATH", str(workspace / "state" / "recorder.json"))
        )
        recorder = CapitalDataRecorder(
            ParquetCandleStore(lake_root),
            symbols_by_epic,
            status_path,
        )
    except Exception as exc:
        _LOG.warning("Recorder context unavailable (%s)", type(exc).__name__)
    runtime_retry_task: asyncio.Task[None] | None = None
    try:
        _install_runtime_services(
            _app,
            settings,
            configs,
            recorder=recorder,
            capital_client=capital_client,
        )
    except Exception as exc:
        # Keep controls available while recording one clear fail-safe alert;
        # _start_runtime_if_configured will not compose a partial runtime.
        detail = _runtime_failure_detail(exc)
        _LOG.error("runtime alert: dependencies unavailable (%s)", detail)
        if runtime_expected:
            set_runtime_health("runtime_unavailable", detail)
        if runtime_expected:
            runtime_retry_task = asyncio.create_task(
                _runtime_retry_loop(
                    _app,
                    settings,
                    configs,
                    recorder=recorder,
                    capital_client=capital_client,
                )
            )

    # Reports run in the core process and use the same Telegram transport and
    # owner allow-list as the interactive channel.  Delivery is optional and
    # never blocks startup when SMTP/Telegram settings are absent.
    report_scheduler = getattr(_app.state, "runtime_services", None)
    report_scheduler = (
        report_scheduler.get("report_scheduler") if isinstance(report_scheduler, dict) else None
    )
    if report_scheduler is not None and hasattr(report_scheduler, "configure_delivery"):
        token = (
            settings.telegram_bot_token.get_secret_value()
            if settings.telegram_bot_token is not None
            else ""
        )
        owner_ids = tuple(
            value.strip()
            for value in (settings.telegram_owner_chat_ids or "").split(",")
            if value.strip()
        )
        report_transport = TelegramBotTransport(token) if token and owner_ids else None
        _app.state.report_telegram_transport = report_transport
        report_scheduler.configure_delivery(
            telegram=report_transport,
            email=(
                _app.state.runtime_services.get("report_email")
                if isinstance(_app.state.runtime_services, dict)
                else None
            ),
            owner_chat_ids=owner_ids,
        )
    _start_runtime_if_configured(_app, settings, configs)
    runtime = getattr(_app.state, "runtime", None)
    if runtime is not None:
        try:
            await runtime.start()
            _start_tai_feed_tailer(_app)
            set_runtime_health(None)
        except Exception as exc:
            detail = _runtime_failure_detail(exc)
            _LOG.error("runtime alert: startup unavailable (%s)", detail)
            set_runtime_health("runtime_unavailable", detail)
            if runtime_retry_task is None and runtime_expected:
                runtime_retry_task = asyncio.create_task(
                    _runtime_retry_loop(
                        _app,
                        settings,
                        configs,
                        recorder=recorder,
                        capital_client=capital_client,
                    )
                )
    elif runtime_expected and runtime_retry_task is None:
        set_runtime_health("runtime_unavailable", "runtime composition unavailable")
        runtime_retry_task = asyncio.create_task(
            _runtime_retry_loop(
                _app,
                settings,
                configs,
                recorder=recorder,
                capital_client=capital_client,
            )
        )
    # Keep the loaded schedule available to the report runner and channel wiring.
    # The scheduler is owned by the runtime process; exposing the validated config
    # here avoids duplicating config reads or accidentally using defaults.
    _app.state.report_schedule = configs["schedule"]
    if settings.role in {"all", "core"}:
        token = (
            settings.telegram_bot_token.get_secret_value()
            if settings.telegram_bot_token is not None
            else ""
        )
        owner_ids = tuple(
            value.strip()
            for value in (settings.telegram_owner_chat_ids or "").split(",")
            if value.strip()
        )
        if not token:
            _LOG.info("Telegram bot token is not configured; Telegram channel disabled")
        elif not owner_ids:
            _LOG.warning("Telegram owner chat ids are not configured; Telegram channel disabled")
        else:
            source = _app.state.execution_control_source
            writer = _app.state.execution_control_writer

            def current_mode() -> ControlMode:
                if source is None:
                    return ControlMode.PAUSE
                try:
                    return source.read()
                except Exception:
                    return ControlMode.PAUSE

            def control_handler(command: str) -> str:
                modes = {
                    "/pause": ControlMode.PAUSE,
                    "/stop": ControlMode.PAUSE,
                    "/kill": ControlMode.KILL,
                }
                mode = modes.get(command)
                if mode is None or writer is None:
                    return "Execution control is unavailable; execution remains halted."
                try:
                    writer.set(mode)
                except Exception:
                    return "Execution control is unavailable; execution remains halted."
                return f"{command} accepted; execution mode is {mode.value}."

            command_service = getattr(_app.state, "command_data_service", None)
            command_handlers = (
                command_service.handlers()
                if isinstance(command_service, CommandDataService)
                else {}
            )
            lab_manager = getattr(_app.state, "lab_job_manager", None)
            if isinstance(lab_manager, LabJobManager):
                command_handlers["/lab"] = lab_manager.handle

            def render_chart(command: str, argument: str) -> bytes:
                if command == "/lab" and isinstance(lab_manager, LabJobManager):
                    return lab_manager.chart(command, argument)
                return (
                    command_service.chart(command, argument)
                    if isinstance(command_service, CommandDataService)
                    else _chart_png(command, argument)
                )

            router = _build_chat_router(
                settings,
                configs,
                control_handler=control_handler,
                command_handlers=command_handlers,
            )
            transport = _report_telegram_transport(_app.state, token)
            channel = TelegramChannel(
                transport,
                owner_ids,
                control_writer=writer,
                current_mode=current_mode,
                router=router,
                render=_command_renderer(command_handlers),
                render_chart=render_chart,
                authorize_resume=lambda: settings.owner_totp_secret is None,
            )
            if report_scheduler is not None and hasattr(report_scheduler, "configure_alert"):
                report_scheduler.configure_alert(channel.send_alert)
            stop_event = threading.Event()
            thread = threading.Thread(
                target=channel.run,
                kwargs={"stop": stop_event.is_set},
                name="telegram-long-poll",
                daemon=True,
            )
            _app.state.telegram_channel = channel
            _app.state.telegram_stop = stop_event
            _app.state.telegram_thread = thread
            thread.start()
    try:
        yield
    finally:
        if runtime_retry_task is not None:
            runtime_retry_task.cancel()
            try:
                await runtime_retry_task
            except asyncio.CancelledError:
                pass
        runtime = getattr(_app.state, "runtime", None)
        if runtime is not None:
            await runtime.stop()
        tai_feed_task = getattr(_app.state, "tai_feed_task", None)
        if tai_feed_task is not None:
            tai_feed_task.cancel()
            try:
                await tai_feed_task
            except asyncio.CancelledError:
                pass
        shutdown_event = getattr(_app.state, "telegram_stop", None)
        shutdown_thread = getattr(_app.state, "telegram_thread", None)
        if shutdown_event is not None:
            shutdown_event.set()
        if shutdown_thread is not None:
            shutdown_thread.join(timeout=0.5)
        if capital_client is not None:
            capital_client.close()


app = FastAPI(title="AI Trader", version="0.1.0", lifespan=lifespan)
app.include_router(control_router)


@app.get("/health")
def health() -> dict[str, Any]:
    """Expose role and dependency health without credentials or sensitive details."""
    return health_payload()
