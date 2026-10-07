"""Startup remains available when the broker is temporarily unavailable."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pydantic import SecretStr

from trader.api import app as app_module


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
            assert len(attempts) == app_module._RUNTIME_SERVICE_RETRIES

    asyncio.run(exercise())


def test_missing_report_transport_uses_fallback_without_state_attribute() -> None:
    """The degraded path is safe even for callers with a minimal app state."""

    transport = app_module._report_telegram_transport(SimpleNamespace(), "redacted-test-token")
    assert isinstance(transport, app_module.TelegramBotTransport)
