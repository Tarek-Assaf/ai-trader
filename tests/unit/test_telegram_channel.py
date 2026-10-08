from __future__ import annotations

import logging

import pytest

import trader.channels.telegram as telegram_module

from trader.channels.telegram import (
    Alert,
    AlertLimiter,
    NonceStore,
    TelegramBotTransport,
    TelegramChannel,
)
from trader.execution.control import ControlMode


class FakeTransport:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, object]] = []
        self.photos: list[tuple[str, bytes, str]] = []
        self.callbacks: list[tuple[str, str]] = []
        self.updates: list[dict[str, object]] = []
        self.fail = False

    def get_updates(self, offset: int | None, timeout: int):
        return self.updates

    def send_message(self, chat_id: str, text: str, *, reply_markup=None) -> None:
        if self.fail:
            raise RuntimeError("delivery failed")
        self.messages.append((chat_id, text, reply_markup))

    def send_photo(self, chat_id: str, photo: bytes, *, caption: str = "") -> None:
        self.photos.append((chat_id, photo, caption))

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.callbacks.append((callback_id, text))


class FakeWriter:
    def __init__(self, mode: ControlMode = ControlMode.RUN) -> None:
        self.mode = mode
        self.set_modes: list[ControlMode] = []

    def set(self, mode: ControlMode) -> None:
        self.mode = mode
        self.set_modes.append(mode)


class FakeAudit:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)


def make_channel(transport: FakeTransport, **kwargs) -> tuple[TelegramChannel, FakeWriter]:
    writer = kwargs.pop("control_writer", FakeWriter())
    channel = TelegramChannel(transport, ["owner"], control_writer=writer, **kwargs)
    return channel, writer


def callback_update(nonce: str, action: str = "confirm_resume") -> dict[str, object]:
    return {
        "callback_query": {
            "id": "callback-1",
            "data": f"{action}:{nonce}",
            "message": {"chat": {"id": "owner"}},
        }
    }


def test_constructor_requires_control_writer() -> None:
    with pytest.raises(ValueError, match="control writer"):
        TelegramChannel(FakeTransport(), ["owner"])


def test_non_owner_message_and_callback_are_ignored() -> None:
    transport = FakeTransport()
    channel, _ = make_channel(transport)
    channel.handle_message("intruder", "/pause")
    channel.handle_update(
        {
            "callback_query": {
                "id": "callback-1",
                "data": "confirm_resume:any",
                "message": {"chat": {"id": "intruder"}},
            }
        }
    )
    assert transport.messages == []
    assert transport.callbacks == []


@pytest.mark.parametrize(
    ("command", "mode"),
    [("/pause", ControlMode.PAUSE), ("/stop", ControlMode.PAUSE), ("/kill", ControlMode.KILL)],
)
def test_safe_control_commands_write_and_audit(command: str, mode: ControlMode) -> None:
    transport = FakeTransport()
    audit = FakeAudit()
    channel, writer = make_channel(transport, audit=audit)
    channel.handle_message("owner", command)
    assert writer.set_modes == [mode]
    assert transport.messages[-1][1].endswith(f"{mode.value}.")
    assert audit.events[-1].event_type == f"telegram_{command}"


def test_flatten_exception_keeps_execution_halted() -> None:
    transport = FakeTransport()
    audit = FakeAudit()

    def flatten() -> bool:
        raise RuntimeError("broker unavailable")

    channel, writer = make_channel(transport, flatten=flatten, audit=audit)
    channel.handle_message("owner", "/stop")
    assert writer.mode is ControlMode.PAUSE
    assert "incomplete" in transport.messages[-1][1]
    assert audit.events[-1].event_type == "telegram_/stop"


def test_data_commands_route_through_render() -> None:
    transport = FakeTransport()
    calls: list[tuple[str, str]] = []

    def render(command: str, argument: str) -> str:
        calls.append((command, argument))
        return "rendered"

    channel, _ = make_channel(transport, render=render)
    for command in ("/status", "/positions", "/pnl", "/risk", "/report daily", "/chart US100 1m"):
        channel.handle_message("owner", command)
    assert calls == [
        ("/status", ""),
        ("/positions", ""),
        ("/pnl", ""),
        ("/risk", ""),
        ("/report", "daily"),
        ("/chart", "US100 1m"),
    ]
    assert len(transport.messages) == len(calls)


def test_chart_command_renders_and_sends_png() -> None:
    transport = FakeTransport()
    calls: list[tuple[str, str]] = []

    def render_chart(command: str, argument: str) -> bytes:
        calls.append((command, argument))
        return b"fake-png"

    channel, _ = make_channel(transport, render_chart=render_chart)
    channel.handle_message("owner", "/chart US100 1m")
    assert calls == [("/chart", "US100 1m")]
    assert transport.photos == [("owner", b"fake-png", "Chart: US100 1m")]
    assert transport.messages == []


def test_chart_renderer_failure_is_safe() -> None:
    transport = FakeTransport()

    def render_chart(command: str, argument: str) -> bytes:
        raise RuntimeError("plot failed")

    channel, _ = make_channel(transport, render_chart=render_chart)
    channel.handle_message("owner", "/chart US100")
    assert transport.photos == []
    assert transport.messages[-1][1] == "Chart is unavailable right now."


def test_resume_requires_readable_mode_and_explicit_authorization() -> None:
    transport = FakeTransport()
    channel, writer = make_channel(transport)
    channel.handle_message("owner", "/resume")
    assert "cannot be read" in transport.messages[-1][1]
    assert writer.set_modes == []

    transport = FakeTransport()
    channel, writer = make_channel(transport, current_mode=lambda: ControlMode.PAUSE)
    channel.handle_message("owner", "/resume")
    markup = transport.messages[-1][2]
    nonce = markup["inline_keyboard"][0][0]["callback_data"].split(":", 1)[1]
    channel.handle_update(callback_update(nonce))
    assert transport.callbacks[-1][1] == "Owner authorization failed"
    assert writer.set_modes == []


def test_kill_is_not_resumable_and_unreadable_mode_fails_closed() -> None:
    for current_mode in (lambda: ControlMode.KILL, lambda: (_ for _ in ()).throw(RuntimeError())):
        transport = FakeTransport()
        channel, writer = make_channel(
            transport, current_mode=current_mode, authorize_resume=lambda: True
        )
        channel.handle_message("owner", "/resume")
        assert "Resume unavailable" in transport.messages[-1][1]
        assert writer.set_modes == []


def test_cancel_consumes_nonce() -> None:
    transport = FakeTransport()
    channel, writer = make_channel(
        transport, current_mode=lambda: ControlMode.PAUSE, authorize_resume=lambda: True
    )
    channel.handle_message("owner", "/resume")
    markup = transport.messages[-1][2]
    nonce = markup["inline_keyboard"][0][1]["callback_data"].split(":", 1)[1]
    channel.handle_update(callback_update(nonce, "cancel"))
    channel.handle_update(callback_update(nonce))
    assert transport.callbacks[0][1] == "Cancelled"
    assert "Expired" in transport.callbacks[-1][1]
    assert writer.set_modes == []


def test_nonce_expiry_and_issue_pruning() -> None:
    now = [0.0]
    store = NonceStore(ttl_seconds=1, clock=lambda: now[0])
    first = store.issue("resume")
    now[0] = 2.0
    second = store.issue("resume")
    assert not store.consume(first, "resume")
    assert store.consume(second, "resume")


def test_alert_limiter_and_delivery_retry() -> None:
    now = [0.0]
    limiter = AlertLimiter(interval_seconds=10, clock=lambda: now[0])
    assert limiter.allow("x")
    assert not limiter.allow("x")
    now[0] = 11
    assert limiter.allow("x")

    transport = FakeTransport()
    channel, _ = make_channel(transport)
    transport.fail = True
    assert channel.send_alert(Alert("down", "broker down"))
    assert transport.messages == []
    transport.fail = False
    channel.flush()
    assert transport.messages[-1][1] == "broker down"


def test_transport_sanitizes_token_errors_and_logs(monkeypatch, caplog) -> None:
    token = "123:secret-token"

    def fail(*args, **kwargs):
        raise RuntimeError(f"request URL leaked {token}")

    import httpx

    monkeypatch.setattr(httpx, "post", fail)
    transport = TelegramBotTransport(token)
    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError, match="Telegram API request failed") as exc_info:
            transport.get_updates(None, 1)
    assert token not in str(exc_info.value)
    assert token not in caplog.text


def test_transport_logs_status_and_telegram_error_code_without_url(monkeypatch, caplog) -> None:
    token = "123:secret-token"

    class Response:
        status_code = 429

        @staticmethod
        def json():
            return {"ok": False, "error_code": 429, "description": "Too Many Requests"}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Response())
    transport = TelegramBotTransport(token)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="Telegram API request failed"):
            transport.get_updates(None, 1)

    assert "status=429" in caplog.text
    assert "telegram_error_code=429" in caplog.text
    assert token not in caplog.text
    assert "api.telegram.org" not in caplog.text


def test_poll_failures_back_off_and_reset(monkeypatch) -> None:
    channel, _ = make_channel(FakeTransport())
    calls = [0]
    delays: list[float] = []

    def poll_once(*, timeout: int) -> int:
        del timeout
        calls[0] += 1
        if calls[0] <= 3:
            raise RuntimeError("poll failed")
        return 0

    monkeypatch.setattr(channel, "poll_once", poll_once)
    monkeypatch.setattr(telegram_module.time, "sleep", delays.append)

    channel.run(stop=lambda: calls[0] >= 4)

    assert delays == [1.0, 2.0, 4.0]
