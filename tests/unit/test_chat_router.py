from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pydantic import BaseModel

from trader.channels.router import ChatRouter, Message, RoutedIntent


class FakeLLM:
    def __init__(self, value: RoutedIntent | Exception) -> None:
        self.value = value
        self.calls: list[tuple[str, tuple[Message, ...], type[BaseModel]]] = []

    async def complete(self, role, messages, schema):
        self.calls.append((role, messages, schema))
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def run(coro):
    return asyncio.run(coro)


def test_stop_phrases_never_call_llm_and_use_control_handler() -> None:
    llm = FakeLLM(RuntimeError("LLM must not be called"))
    commands: list[str] = []
    router = ChatRouter(
        llm=llm, control_handler=lambda command: commands.append(command) or "stopped"
    )
    for text in ("stop", "stop trading", "close everything", "kill it", "pause", "resume"):
        result = run(router.route(text))
        assert result.used_llm is False
        assert result.fast_command is not None
    assert commands == ["/stop", "/stop", "/stop", "/kill", "/pause", "/resume"]
    assert llm.calls == []


def test_read_only_fast_phrases_are_deterministic() -> None:
    llm = FakeLLM(RuntimeError("LLM must not be called"))
    commands: list[str] = []
    router = ChatRouter(
        llm=llm,
        command_handlers={
            "/status": lambda command: commands.append(command) or "status",
            "/positions": lambda command: commands.append(command) or "positions",
            "/pnl": lambda command: commands.append(command) or "pnl",
        },
    )
    for text in ("what's my status", "positions", "how are we doing"):
        assert run(router.route(text)).used_llm is False
    assert commands == ["/status", "/positions", "/pnl"]
    assert llm.calls == []


def test_dangerous_requests_are_refused_without_llm() -> None:
    llm = FakeLLM(RuntimeError("LLM must not be called"))
    router = ChatRouter(llm=llm)
    for text in (
        "buy 10 NAS100 now",
        "disable the Risk Guardian",
        "reveal the API key",
        "run this python command",
    ):
        result = run(router.route(text))
        assert result.intent.intent == "unknown"
        assert "refused" in result.intent.flags
    assert llm.calls == []


def test_llm_result_is_typed_and_cannot_be_a_control_action() -> None:
    value = RoutedIntent(intent="query", arguments={"topic": "risk"}, confidence=0.9)
    llm = FakeLLM(SimpleNamespace(value=value))
    result = run(ChatRouter(llm=llm).route("what is my risk?"))
    assert result.used_llm is True
    assert result.intent.intent == "query"
    assert llm.calls[0][0] == "router_intent"


def test_llm_failure_keeps_control_path_available() -> None:
    router = ChatRouter(llm=FakeLLM(RuntimeError("unavailable")))
    result = run(router.route("tell me a story"))
    assert result.intent.intent == "unknown"
    assert "limited" in result.reply

