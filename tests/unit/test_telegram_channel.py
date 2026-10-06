from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from trader.channels.telegram import Alert, AlertLimiter, NonceStore, TelegramChannel
from trader.execution.control import ControlMode


class FakeTransport:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, Mapping[str, object] | None]] = []
        self.callbacks: list[tuple[str, str]] = []
        self.updates: list[Mapping[str, object]] = []
        self.fail = False

    def get_updates(self, offset: int | None, timeout: int) -> Sequence[Mapping[str, object]]:
        return self.updates

    def send_message(self, chat_id: str, text: str, *, reply_markup=None) -> None:
        if self.fail:
            raise RuntimeError("offline")
        self.messages.append((chat_id, text, reply_markup))

    def send_photo(self, chat_id: str, photo: bytes, *, caption: str = "") -> None:
        raise AssertionError("not used")

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.callbacks.append((callback_id, text))


class FakeWriter:
    def __init__(self) -> None:
        self.modes: list[ControlMode] = []

    def set(self, mode: ControlMode) -> None:
        self.modes.append(mode)


def test_non_owner_is_ignored_without_reply() -> None:
    transport = FakeTransport()
    channel = TelegramChannel(transport, ["owner"], control_writer=FakeWriter())

    channel.handle_message("other", "/status")

    assert transport.messages == []


@pytest.mark.parametrize(
    "command,mode",
    [("/pause", ControlMode.PAUSE), ("/stop", ControlMode.PAUSE), ("/kill", ControlMode.KILL)],
)
def test_safe_control_commands_are_deterministic(command: str, mode: ControlMode) -> None:
    transport = FakeTransport()
    writer = FakeWriter()
    channel = TelegramChannel(transport, ["owner"], control_writer=writer, flatten=lambda: True)

    channel.handle_message("owner", command)

    assert writer.modes == [mode]
    assert transport.messages


def test_resume_requires_single_use_nonce_and_owner_gate() -> None:
    transport = FakeTransport()
    writer = FakeWriter()
    calls: list[bool] = []
    channel = TelegramChannel(
        transport,
        ["owner"],
        control_writer=writer,
        authorize_resume=lambda: calls.append(True) or True,
    )

    channel.handle_message("owner", "/resume")
    markup = transport.messages[-1][2]
    assert markup is not None
    nonce_data = markup["inline_keyboard"][0][0]["callback_data"]  # type: ignore[index]
    channel.handle_update(
        {"callback_query": {"id": "cb-1", "data": nonce_data, "message": {"chat": {"id": "owner"}}}}
    )
    channel.handle_update(
        {"callback_query": {"id": "cb-2", "data": nonce_data, "message": {"chat": {"id": "owner"}}}}
    )

    assert calls == [True]
    assert writer.modes == [ControlMode.RESUME]
    assert transport.callbacks[-1][1] == "Expired or invalid confirmation"


def test_alerts_are_rate_limited_by_stable_key() -> None:
    transport = FakeTransport()
    channel = TelegramChannel(transport, ["owner"], alert_limiter=AlertLimiter(interval_seconds=60))

    assert channel.send_alert(Alert("stale-feed", "stale"))
    assert not channel.send_alert(Alert("stale-feed", "stale"))
    assert [message[1] for message in transport.messages] == ["stale"]


def test_delivery_failure_is_queued_for_retry() -> None:
    transport = FakeTransport()
    channel = TelegramChannel(transport, ["owner"])
    transport.fail = True
    channel.handle_message("owner", "/help")
    transport.fail = False
    channel.flush()

    assert transport.messages


def test_nonce_store_rejects_expired_nonce() -> None:
    now = [10.0]
    store = NonceStore(ttl_seconds=1, clock=lambda: now[0])
    nonce = store.issue("resume")
    now[0] = 12.0
    assert not store.consume(nonce, "resume")
