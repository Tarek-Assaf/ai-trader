"""Offline tests for configured LLM routing, provider safety and spend caps."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine, select

from trader.agents.llm import (
    AnthropicProvider,
    BudgetExceeded,
    LLMClient,
    LLMError,
    Message,
    MockLLM,
    ModelUnavailable,
    OpenAICompatibleProvider,
    ProviderReply,
    SqlUsageLedger,
)
from trader.core.db import tables


class Decision(BaseModel):
    action: str


def config() -> dict[str, Any]:
    return {
        "providers": {},
        "tiers": {
            "cheap": {
                "provider": "cheap",
                "model": "cheap-model",
                "max_input_tokens": 10,
                "max_output_tokens": 2,
                "input_usd_per_million": 1.0,
                "output_usd_per_million": 1.0,
            },
            "mid": {
                "provider": "mid",
                "model": "mid-model",
                "max_input_tokens": 10,
                "max_output_tokens": 2,
                "input_usd_per_million": 1.0,
                "output_usd_per_million": 1.0,
            },
        },
        "roles": {"planner": "cheap"},
        "fallback": {"cheap": "mid", "mid": "cheap"},
        "budget": {"daily_usd_cap": 4.0, "monthly_usd_cap": 80.0},
        "health_probe": {
            "interval_minutes": 15,
            "timeout_seconds": 0.05,
            "failures_to_open": 1,
            "cooldown_minutes": 5,
        },
    }


def ledger(engine: Any, *, daily: str = "4", monthly: str = "80") -> SqlUsageLedger:
    return SqlUsageLedger(
        engine,
        daily_cap_usd=Decimal(daily),
        monthly_cap_usd=Decimal(monthly),
        clock=lambda: datetime(2026, 10, 4, 12, tzinfo=UTC),
    )


def make_client(providers: dict[str, Any], usage: SqlUsageLedger) -> LLMClient:
    return LLMClient(config=config(), providers=providers, ledger=usage)


def test_role_config_selects_model_and_returns_typed_usage() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    provider = MockLLM([ProviderReply('{"action":"hold"}', 3, 1)])
    client = make_client({"cheap": provider}, ledger(engine))

    result = asyncio.run(client.complete("planner", (Message("user", "review"),), Decision))

    assert result.value.action == "hold"
    assert result.model == "cheap-model"
    assert result.input_tokens == 3
    assert result.output_tokens == 1
    assert result.cost_usd == Decimal("0.000004")
    assert provider.calls == 1


def test_factory_loads_approved_caps_without_hosted_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    settings = config()
    settings["providers"] = {
        "anthropic": {
            "kind": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key_env": "T042_TEST_API_KEY",
        }
    }
    monkeypatch.delenv("T042_TEST_API_KEY", raising=False)

    client = LLMClient.from_environment(config=settings, engine=engine)

    assert client.ledger.daily_cap_usd == Decimal("4.0")
    assert client.ledger.monthly_cap_usd == Decimal("80.0")
    assert client.providers == {}


def test_role_tier_change_is_only_a_config_change() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    settings = config()
    settings["roles"]["planner"] = "mid"
    provider = MockLLM([ProviderReply('{"action":"hold"}', 1, 1)])
    client = LLMClient(config=settings, providers={"mid": provider}, ledger=ledger(engine))

    result = asyncio.run(client.complete("planner", (Message("user", "review"),), Decision))

    assert result.model == "mid-model"


def test_invalid_output_routes_to_configured_fallback() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    bad = MockLLM([ProviderReply('{"wrong":"shape"}', 2, 1)])
    fallback = MockLLM([ProviderReply('{"action":"hold"}', 2, 1)])
    client = make_client({"cheap": bad, "mid": fallback}, ledger(engine))

    result = asyncio.run(client.complete("planner", (Message("user", "review"),), Decision))

    assert result.model == "mid-model"
    assert result.fallback_used is True
    assert bad.calls == fallback.calls == 1
    with engine.connect() as connection:
        outcomes = connection.execute(select(tables["llm_usage"].c.outcome)).scalars().all()
    assert outcomes == ["invalid_output", "success"]


def test_transport_failure_falls_back_without_leaking_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingProvider:
        async def complete(self, **kwargs: Any) -> ProviderReply:
            del kwargs
            raise LLMError("token=must-not-leak")

    engine = create_engine("sqlite+pysqlite:///:memory:")
    fallback = MockLLM([ProviderReply('{"action":"hold"}', 1, 1)])
    client = make_client({"cheap": FailingProvider(), "mid": fallback}, ledger(engine))
    caplog.set_level(logging.DEBUG)

    result = asyncio.run(client.complete("planner", (Message("user", "review"),), Decision))

    assert result.fallback_used is True
    assert "must-not-leak" not in caplog.text


def test_daily_cap_blocks_before_provider_call() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    usage = ledger(engine, daily="0.000012", monthly="1")
    usage.reserve(role="prior", model="cheap-model", maximum_cost=Decimal("0.000012"))
    provider = MockLLM([ProviderReply('{"action":"hold"}', 1, 1)])
    client = make_client({"cheap": provider}, usage)

    with pytest.raises(BudgetExceeded):
        asyncio.run(client.complete("planner", (Message("user", "review"),), Decision))
    assert provider.calls == 0


def test_monthly_cap_is_checked_independently() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    usage = ledger(engine, daily="10", monthly="0.000012")
    usage.reserve(role="prior", model="cheap-model", maximum_cost=Decimal("0.000012"))

    with pytest.raises(BudgetExceeded):
        usage.reserve(role="planner", model="cheap-model", maximum_cost=Decimal("0.000001"))


def test_probe_timeout_marks_models_unhealthy_and_blocks_unavailable_role() -> None:
    class HangingProvider:
        async def complete(self, **kwargs: Any) -> ProviderReply:
            del kwargs
            await asyncio.sleep(1)
            return ProviderReply('{"ok":true}', 1, 1)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    client = make_client({"cheap": HangingProvider()}, ledger(engine))

    results = asyncio.run(client.run_health_probes())

    assert results == {"cheap": False, "mid": False}
    with pytest.raises(ModelUnavailable):
        asyncio.run(client.complete("planner", (Message("user", "review"),), Decision))


def test_anthropic_provider_sends_key_only_in_header_and_maps_response() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": '{"action":"hold"}'}],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        )

    async def run() -> ProviderReply:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            provider = AnthropicProvider(api_key="sensitive", client=transport)
            return await provider.complete(
                model="m",
                messages=(Message("user", "review"),),
                schema=Decision,
                max_output_tokens=2,
            )

    reply = asyncio.run(run())

    assert reply == ProviderReply('{"action":"hold"}', 4, 2)
    assert requests[0].headers["x-api-key"] == "sensitive"
    assert b"sensitive" not in requests[0].content


def test_openai_provider_maps_chat_completion_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "m"
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"action":"hold"}'}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    async def run() -> ProviderReply:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            provider = OpenAICompatibleProvider(
                base_url="https://api.example.test/v1", client=transport
            )
            return await provider.complete(
                model="m",
                messages=(Message("user", "review"),),
                schema=Decision,
                max_output_tokens=2,
            )

    assert asyncio.run(run()) == ProviderReply('{"action":"hold"}', 2, 1)


def test_openai_provider_rejects_redirect_and_endpoint_credentials() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(302, headers={"location": "https://attacker.invalid"})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            provider = OpenAICompatibleProvider(
                base_url="https://api.example.test/v1", client=transport
            )
            await provider.complete(
                model="m",
                messages=(Message("user", "review"),),
                schema=Decision,
                max_output_tokens=2,
            )

    with pytest.raises(LLMError, match="redirect"):
        asyncio.run(run())
    with pytest.raises(ValueError, match="credential-free"):
        OpenAICompatibleProvider(base_url="https://user:pass@example.test/v1")


def test_remote_failure_does_not_include_response_body_or_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, text="secret payload")

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            provider = OpenAICompatibleProvider(
                base_url="https://api.example.test/v1", api_key="sensitive", client=transport
            )
            await provider.complete(
                model="m",
                messages=(Message("user", "review"),),
                schema=Decision,
                max_output_tokens=2,
            )

    caplog.set_level(logging.DEBUG)
    with pytest.raises(LLMError, match="HTTP 500") as error:
        asyncio.run(run())
    assert "secret payload" not in str(error.value)
    assert "sensitive" not in caplog.text


def test_oversized_remote_response_is_rejected_while_streaming() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=b"x" * 2_000_001,
            headers={"content-type": "application/json"},
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            provider = OpenAICompatibleProvider(
                base_url="https://api.example.test/v1", client=transport
            )
            await provider.complete(
                model="m",
                messages=(Message("user", "review"),),
                schema=Decision,
                max_output_tokens=2,
            )

    with pytest.raises(LLMError, match="size limit"):
        asyncio.run(run())


def test_prompt_roles_must_be_supported_before_call() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    provider = MockLLM([ProviderReply('{"action":"hold"}', 1, 1)])
    client = make_client({"cheap": provider}, ledger(engine))

    with pytest.raises(ValueError, match="known role"):
        asyncio.run(client.complete("planner", (Message("tool", "unsafe"),), Decision))
    assert provider.calls == 0
