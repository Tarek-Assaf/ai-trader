"""Runtime wiring tests for the owner Telegram channel."""

from __future__ import annotations

from typing import Any

from trader.channels.telegram import TelegramChannel
from trader.execution.control import ControlMode


class FakeTransport:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        del offset, timeout
        return []

    def send_message(self, chat_id: str, text: str, *, reply_markup: Any = None) -> None:
        del reply_markup
        self.messages.append((chat_id, text))

    def send_photo(self, chat_id: str, photo: bytes, *, caption: str = "") -> None:
        del photo, caption
        self.messages.append((chat_id, "photo"))

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        del callback_id, text


class FakeWriter:
    def __init__(self) -> None:
        self.mode = ControlMode.RUN

    def set(self, mode: ControlMode) -> None:
        self.mode = mode


class FakeRouter:
    async def route(self, text: str) -> Any:
        class Result:
            reply = f"echo: {text}"

        return Result()


def test_non_command_owner_text_uses_router_and_non_owner_is_silent() -> None:
    transport = FakeTransport()
    channel = TelegramChannel(
        transport,
        ["owner"],
        control_writer=FakeWriter(),
        router=FakeRouter(),
    )

    channel.handle_message("owner", "what is my status?")
    channel.handle_message("other", "what is my status?")

    assert transport.messages == [("owner", "echo: what is my status?")]


def test_router_failure_keeps_channel_bounded() -> None:
    class RaisingRouter:
        async def route(self, text: str) -> Any:
            del text
            raise RuntimeError("model unavailable")

    transport = FakeTransport()
    channel = TelegramChannel(
        transport,
        ["owner"],
        control_writer=FakeWriter(),
        router=RaisingRouter(),
    )

    channel.handle_message("owner", "tell me a story")

    assert transport.messages == [
        ("owner", "The assistant is limited right now; /status and /stop still work.")
    ]


def test_commands_remain_sync_when_router_is_unavailable() -> None:
    transport = FakeTransport()
    writer = FakeWriter()
    channel = TelegramChannel(transport, ["owner"], control_writer=writer)

    channel.handle_message("owner", "/pause")

    assert writer.mode is ControlMode.PAUSE
    assert transport.messages[-1][0] == "owner"
