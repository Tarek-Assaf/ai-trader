"""Startup remains available when the broker is temporarily unavailable."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pydantic import SecretStr

from trader.api import app as app_module
from trader.api import health as health_module
from trader.channels.telegram import TelegramChannel
from trader.reports.commands import CommandDataService


class _CommandTransport:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def get_updates(self, _offset: int | None, _timeout: int) -> list[dict[str, object]]:
        return []

    def send_message(self, chat_id: str, text: str, *, reply_markup=None) -> None:
        del reply_markup
        self.messages.append((chat_id, text))

    def send_photo(self, _chat_id: str, _photo: bytes, *, caption: str = "") -> None:
        del caption

    def answer_callback(self, _callback_id: str, _text: str = "") -> None:
        return None


class _CommandWriter:
    def set(self, _mode) -> None:
        return None


class _PaperBroker:
    def get_account(self) -> dict[str, str]:
        return {"balance": "10000", "equity": "10000", "currency": "USD"}

    def get_positions(self) -> tuple[object, ...]:
        return ()


def test_app_command_renderer_uses_real_command_service_and_paper_broker() -> None:
    """The real channel wiring renders /status from the paper account."""
    transport = _CommandTransport()
    service = CommandDataService(_PaperBroker())
    channel = TelegramChannel(
        transport,
        ["owner"],
        control_writer=_CommandWriter(),
        render=app_module._command_renderer(service.handlers()),
    )

    channel.handle_message("owner", "/status")

    assert transport.messages[-1][1].startswith(
        "Status: account USD; balance 10000; equity 10000."
    )
    assert transport.messages[-1][1] != "/status:"

    channel.handle_message("owner", "/soak")
    assert transport.messages[-1][1] == "Soak status is unavailable right now."


def test_app_command_renderer_reports_bounded_unavailable_without_broker() -> None:
    """A missing broker uses the bounded response instead of the echo placeholder."""
    transport = _CommandTransport()
    service = CommandDataService(None)
    channel = TelegramChannel(
        transport,
        ["owner"],
        control_writer=_CommandWriter(),
        render=app_module._command_renderer(service.handlers()),
    )

    channel.handle_message("owner", "/status")

    assert transport.messages[-1][1] == "Trading status is unavailable right now."
    assert "/status:" not in transport.messages[-1][1]


def test_app_command_renderer_passes_full_command_and_bounds_failures(caplog) -> None:
    seen: list[str] = []

    def fail(command: str) -> str:
        seen.append(command)
        raise RuntimeError("secret handler detail")

    rendered = app_module._command_renderer({"/report": fail})

    with caplog.at_level("WARNING"):
        assert rendered("/report", "weekly") == "/report is unavailable right now."

    assert seen == ["/report weekly"]
    assert "RuntimeError" in caplog.text
    assert "secret handler detail" not in caplog.text


def test_lifespan_keeps_telegram_startup_alive_when_runtime_services_fail(monkeypatch) -> None:
    """A broker 429 must leave the API degraded, not crash on missing state."""

    class _Settings:
        role = "core"
        redis_url = None
        capital_api_key = None
        capital_identifier = None
        capital_api_password = None
        telegram_bot_token = SecretStr("redacted-test-token")
        telegram_owner_chat_ids = "123"
        owner_totp_secret = None
        llm_provider = "mock"
        anthropic_api_key = None

        def sensitive_values(self) -> tuple[str, ...]:
            return ()

    class _Channel:
        def __init__(self, transport, *_args, **_kwargs):
            self.transport = transport

        def run(self, *, stop):
            del stop

    configs = {
        "instruments": SimpleNamespace(model_dump=lambda: {"instruments": {}}),
        "schedule": SimpleNamespace(),
        "llm": SimpleNamespace(model_dump=lambda: {}),
    }
    app = SimpleNamespace(state=SimpleNamespace())
    attempts: list[int] = []

    monkeypatch.setattr(app_module, "EnvironmentSettings", _Settings)
    monkeypatch.setattr(app_module, "configure_logging", lambda _values: None)
    monkeypatch.setattr(app_module, "load_all_configs", lambda *_args, **_kwargs: configs)

    def fail_runtime_services(*_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("CapitalCom 429")

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(app_module, "build_runtime_services", fail_runtime_services)
    monkeypatch.setattr(app_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(app_module, "TelegramChannel", _Channel)

    async def exercise() -> None:
        async with app_module.lifespan(app):
            assert isinstance(app.state.telegram_channel.transport, app_module.TelegramBotTransport)
            assert app.state.report_telegram_transport is None
            assert len(attempts) == 1

    asyncio.run(exercise())


def test_missing_report_transport_uses_fallback_without_state_attribute() -> None:
    """The degraded path is safe even for callers with a minimal app state."""

    transport = app_module._report_telegram_transport(SimpleNamespace(), "redacted-test-token")
    assert isinstance(transport, app_module.TelegramBotTransport)


def test_runtime_retry_loop_uses_long_backoff_and_recovers(monkeypatch) -> None:
    """Transient runtime failures retry in the background without blocking startup."""
    app = SimpleNamespace(state=SimpleNamespace(runtime=None))
    attempts: list[int] = []
    delays: list[float] = []

    class _Runtime:
        async def start(self) -> None:
            return None

    def install(*_args, **_kwargs) -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("runtime unavailable")
        app.state.runtime = _Runtime()

    async def sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(app_module, "_install_runtime_services", install)
    monkeypatch.setattr(app_module, "_start_runtime_if_configured", lambda *_args: None)

    asyncio.run(
        app_module._runtime_retry_loop(
            app,
            object(),
            {},
            recorder=None,
            capital_client=None,
            sleep=sleep,
            jitter=lambda _low, _high: 0.0,
        )
    )

    assert len(attempts) == 3
    assert delays == [30.0, 60.0, 120.0]


def test_runtime_retry_loop_keeps_degraded_health_on_persistent_failure(monkeypatch) -> None:
    """Persistent failures keep retrying without escaping into API startup."""
    app = SimpleNamespace(state=SimpleNamespace(runtime=None))
    attempts: list[int] = []
    delays: list[float] = []

    def install(*_args, **_kwargs) -> None:
        attempts.append(1)
        raise RuntimeError("CapitalCom 429")

    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(app_module, "_install_runtime_services", install)
    monkeypatch.setattr(app_module, "_start_runtime_if_configured", lambda *_args: None)

    try:
        asyncio.run(
            app_module._runtime_retry_loop(
                app,
                object(),
                {},
                recorder=None,
                capital_client=None,
                sleep=sleep,
                jitter=lambda _low, _high: 0.0,
            )
        )
    except asyncio.CancelledError:
        pass

    assert attempts == [1]
    assert delays == [30.0, 60.0]
    assert health_module._RUNTIME_HEALTH_REASON == "runtime_unavailable"
    assert health_module._RUNTIME_HEALTH_DETAIL == "RuntimeError (test_api_startup.py:install)"
    app_module.set_runtime_health(None)


def test_runtime_dependency_message_is_logged_and_exposed(monkeypatch, caplog) -> None:
    """Known dependency failures expose their fixed, secret-free message."""
    app = SimpleNamespace(state=SimpleNamespace(runtime=None))
    attempts: list[int] = []

    def install(*_args, **_kwargs) -> None:
        attempts.append(1)
        raise app_module.RuntimeDependencyUnavailable("runtime service unavailable: redis")

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        # Let the first attempt run, then stop the loop on the next wait.
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(app_module, "_install_runtime_services", install)
    monkeypatch.setattr(app_module, "_start_runtime_if_configured", lambda *_args: None)

    with caplog.at_level("WARNING"):
        try:
            asyncio.run(
                app_module._runtime_retry_loop(
                    app,
                    object(),
                    {},
                    recorder=None,
                    capital_client=None,
                    sleep=sleep,
                    jitter=lambda _low, _high: 0.0,
                )
            )
        except asyncio.CancelledError:
            pass

    assert attempts == [1]
    assert "runtime service unavailable: redis" in caplog.text
    assert health_module._RUNTIME_HEALTH_DETAIL == (
        "RuntimeDependencyUnavailable: runtime service unavailable: redis "
        "(test_api_startup.py:install)"
    )
    health_module.set_runtime_health(None)
