from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from trader.channels.router import ChatRouter, Intent, Message, RoutedIntent


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
        llm=llm,
        control_handler=lambda command: commands.append(command) or "stopped",
        command_handlers={"/resume": lambda command: "confirm"},
    )
    for text in ("stop", "stop trading", "close everything", "kill it", "pause", "resume"):
        result = run(router.route(text))
        assert result.used_llm is False
        assert result.fast_command is not None
    assert commands == ["/stop", "/stop", "/stop", "/kill", "/pause"]
    assert llm.calls == []


def test_read_only_fast_phrases_are_deterministic() -> None:
    llm = FakeLLM(RuntimeError("LLM must not be called"))
    commands: list[str] = []
    router = ChatRouter(
        llm=llm,
        control_handler=lambda command: "control",
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
    router = ChatRouter(llm=llm, control_handler=lambda command: "control")
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
    value = RoutedIntent(
        intent="query", arguments={"topic": "risk"}, confidence=0.9, flags=("fast_path", "refused")
    )
    llm = FakeLLM(SimpleNamespace(value=value))
    result = run(
        ChatRouter(llm=llm, control_handler=lambda command: "control").route("what is my risk?")
    )
    assert result.used_llm is True
    assert result.intent.intent == "query"
    assert result.intent.flags == ("llm",)
    assert llm.calls[0][0] == "router_intent"


def test_llm_failure_keeps_control_path_available() -> None:
    commands: list[str] = []
    router = ChatRouter(
        llm=FakeLLM(RuntimeError("unavailable")),
        control_handler=lambda command: commands.append(command) or "stopped",
    )
    result = run(router.route("tell me a story"))
    assert result.intent.intent == "unknown"
    assert "limited" in result.reply
    assert run(router.route("stop")).reply == "stopped"
    assert commands == ["/stop"]


def test_control_handler_is_required_and_resume_uses_confirm_handler() -> None:
    with pytest.raises(ValueError, match="control handler"):
        ChatRouter()  # type: ignore[call-arg]
    commands: list[str] = []
    router = ChatRouter(
        control_handler=lambda command: commands.append(command) or "control",
    )
    result = run(router.route("resume"))
    assert result.reply == "Send /resume and press Confirm."
    assert commands == []


@pytest.mark.parametrize(
    "text",
    (
        "halt",
        "flatten",
        "close all positions",
        "stop everything",
        "emergency stop",
        "kill switch",
        "stop it",
        "stop trading right now",
    ),
)
def test_stop_synonyms_never_call_llm(text: str) -> None:
    llm = FakeLLM(RuntimeError("must not call"))
    commands: list[str] = []
    router = ChatRouter(
        llm=llm, control_handler=lambda command: commands.append(command) or "stopped"
    )
    assert run(router.route(text)).used_llm is False
    assert llm.calls == []
    expected = "/kill" if text in {"emergency stop", "kill switch"} else "/stop"
    assert commands == [expected]


def test_near_miss_control_words_clarify_without_acting() -> None:
    llm = FakeLLM(RuntimeError("must not call"))
    commands: list[str] = []
    router = ChatRouter(
        llm=llm, control_handler=lambda command: commands.append(command) or "stopped"
    )
    result = run(router.route("could you stop after the current candle?"))
    assert "Did you mean" in result.reply
    assert result.used_llm is False
    assert commands == []
    assert llm.calls == []


def test_intent_schema_has_no_control_actions() -> None:
    with pytest.raises(ValidationError):
        Intent(intent="pause")


def test_router_import_boundary_has_no_stateful_modules() -> None:
    tree = ast.parse(Path("src/trader/channels/router.py").read_text(encoding="utf-8"))
    forbidden = (
        "trader.execution",
        "trader.integrations",
        "trader.risk",
        "trader.control.settings",
    )
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert not any(name.startswith(forbidden) for name in imported)
