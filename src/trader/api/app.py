"""FastAPI application for health and operational status."""

from __future__ import annotations

import io
import logging
import os
import threading
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from trader.api.control import router as control_router
from trader.api.health import health_payload
from trader.channels.router import ChatRouter, RoutedIntent
from trader.channels.telegram import TelegramBotTransport, TelegramChannel
from trader.core.config import load_all_configs
from trader.core.logging import configure_logging
from trader.core.settings import EnvironmentSettings
from trader.data.instruments import InstrumentMapper
from trader.execution.control import ControlMode, build_control_sources
from trader.execution.reconciliation import capital_account_field_map_from_config
from trader.integrations.capital_com.client import CapitalComClient
from trader.runtime import runtime_enabled

_LOG = logging.getLogger(__name__)


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
    # Keep the loaded schedule available to the report runner and channel wiring.
    # The scheduler is owned by the runtime process; exposing the validated config
    # here avoids duplicating config reads or accidentally using defaults.
    _app.state.report_schedule = configs["schedule"]
    _app.state.instrument_mapper = None
    capital_client: CapitalComClient | None = None
    has_capital_credentials = all(
        value is not None and bool(value.get_secret_value())
        for value in (
            settings.capital_api_key,
            settings.capital_identifier,
            settings.capital_api_password,
        )
    )
    _app.state.capital_account_fields = None
    if settings.role in {"all", "core"} and has_capital_credentials:
        try:
            _app.state.capital_account_fields = capital_account_field_map_from_config(
                configs["settings"].model_dump()
            )
        except ValueError as exc:
            # Fail closed for P&L reconciliation and order wiring, but keep the API and the
            # owner kill/pause/resume endpoints up: control must never depend on this check.
            _LOG.error(
                "Capital.com account mapping not verified; reconciliation disabled (%s)", exc
            )
    if settings.role in {"all", "core"} and has_capital_credentials:
        try:
            capital_client = CapitalComClient(settings)
            mapper = InstrumentMapper(configs["instruments"].model_dump(), capital_client)
            _app.state.instrument_mapper = mapper
            mapper.refresh_all()
        except Exception as exc:
            _app.state.instrument_mapper = None
            _LOG.warning("Capital.com instrument metadata unavailable (%s)", type(exc).__name__)
            if capital_client is not None:
                capital_client.close()
                capital_client = None
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

            command_handlers = {
                "/status": lambda _command: "Trading status is available.",
                "/positions": lambda _command: "Positions are available.",
                "/pnl": lambda _command: "P&L is available.",
                "/risk": lambda _command: "Risk limits are enforced by Risk Guardian.",
                "/report": lambda _command: "Report delivery is scheduled.",
            }
            router = _build_chat_router(
                settings,
                configs,
                control_handler=control_handler,
                command_handlers=command_handlers,
            )
            channel = TelegramChannel(
                TelegramBotTransport(token),
                owner_ids,
                control_writer=writer,
                current_mode=current_mode,
                router=router,
                render_chart=_chart_png,
                authorize_resume=lambda: settings.owner_totp_secret is None,
            )
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
        runtime = getattr(_app.state, "runtime", None)
        if runtime is not None:
            await runtime.stop()
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
