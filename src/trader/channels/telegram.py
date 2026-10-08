"""Safety-first Telegram long-poll channel.

The channel deliberately depends on a small transport protocol so all command,
allow-list, nonce and rate-limit behaviour can be tested without network calls.
The bot token is supplied only to :class:`TelegramBotTransport` and is never
included in logs or outbound application messages.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol

from trader.execution.control import ControlMode, ControlWriter

LOGGER = logging.getLogger(__name__)


class TelegramTransport(Protocol):
    """Minimal transport used by the channel and its tests."""

    def get_updates(self, offset: int | None, timeout: int) -> Sequence[Mapping[str, Any]]: ...

    def send_message(
        self, chat_id: str, text: str, *, reply_markup: Mapping[str, Any] | None = None
    ) -> None: ...

    def send_photo(self, chat_id: str, photo: bytes, *, caption: str = "") -> None: ...

    def answer_callback(self, callback_id: str, text: str = "") -> None: ...


class AuditSink(Protocol):
    def append(self, event: Any) -> None: ...


class Flattener(Protocol):
    def __call__(self) -> bool: ...


class ChartRenderer(Protocol):
    """Build a headless PNG for an owner chart request."""

    def __call__(self, command: str, argument: str) -> bytes: ...


class MessageRouter(Protocol):
    """Async natural-language router used for non-command owner messages."""

    async def route(self, text: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class Alert:
    """An outbound alert with a stable key for de-duplication."""

    alert_key: str
    text: str


@dataclass(frozen=True, slots=True)
class PendingNonce:
    nonce: str
    action: str
    expires_at: float


class NonceStore:
    """Single-use, short-lived callback nonces kept outside message content."""

    def __init__(
        self, *, ttl_seconds: float = 60.0, clock: Callable[[], float] = time.time
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("nonce TTL must be positive")
        self._ttl = ttl_seconds
        self._clock = clock
        self._values: dict[str, PendingNonce] = {}
        self._lock = Lock()

    def issue(self, action: str) -> str:
        if not action.strip():
            raise ValueError("nonce action must not be empty")
        nonce = secrets.token_urlsafe(18)
        now = self._clock()
        with self._lock:
            self._values = {
                key: value for key, value in self._values.items() if value.expires_at >= now
            }
            self._values[nonce] = PendingNonce(nonce, action, now + self._ttl)
        return nonce

    def consume(self, nonce: str, action: str) -> bool:
        now = self._clock()
        with self._lock:
            item = self._values.pop(nonce, None)
        return item is not None and item.action == action and item.expires_at >= now

    def consume_any(self, nonce: str) -> bool:
        """Consume a nonce without requiring an action, for cancellation."""
        now = self._clock()
        with self._lock:
            item = self._values.pop(nonce, None)
        return item is not None and item.expires_at >= now


class AlertLimiter:
    """Per-key alert limiter; a flapping condition cannot spam the owner."""

    def __init__(
        self, *, interval_seconds: float = 300.0, clock: Callable[[], float] = time.time
    ) -> None:
        if interval_seconds < 0:
            raise ValueError("alert interval cannot be negative")
        self._interval = interval_seconds
        self._clock = clock
        self._sent: dict[str, float] = {}
        self._lock = Lock()

    def allow(self, alert_key: str) -> bool:
        now = self._clock()
        with self._lock:
            previous = self._sent.get(alert_key)
            if previous is not None and now - previous < self._interval:
                return False
            self._sent[alert_key] = now
            return True


class TelegramChannel:
    """Long-poll Telegram channel with deterministic control commands.

    ``render`` callbacks provide application data while this class owns all
    safety-sensitive routing. Non-owner updates are logged by chat-id hash only
    and receive no reply. Outbound delivery failures are queued and retried by
    :meth:`flush`; callers can run it from a worker without blocking execution.
    """

    COMMANDS = {
        "/status",
        "/positions",
        "/pnl",
        "/pause",
        "/resume",
        "/stop",
        "/kill",
        "/risk",
        "/report",
        "/soak",
        "/chart",
        "/help",
    }

    def __init__(
        self,
        transport: TelegramTransport,
        owner_chat_ids: Iterable[str],
        *,
        control_writer: ControlWriter | None = None,
        current_mode: Callable[[], ControlMode] | None = None,
        flatten: Flattener | None = None,
        render: Callable[[str, str], str] | None = None,
        render_chart: Callable[[str, str], bytes] | None = None,
        router: MessageRouter | None = None,
        authorize_resume: Callable[[], bool] | None = None,
        audit: AuditSink | None = None,
        nonce_store: NonceStore | None = None,
        alert_limiter: AlertLimiter | None = None,
        max_queue: int = 128,
    ) -> None:
        ids = frozenset(value.strip() for value in owner_chat_ids if value.strip())
        if not ids:
            raise ValueError("at least one owner Telegram chat id is required")
        if max_queue <= 0:
            raise ValueError("outbound queue size must be positive")
        if control_writer is None:
            raise ValueError("a control writer is required")
        self._transport = transport
        self._owner_ids = ids
        self._control_writer = control_writer
        self._current_mode = current_mode
        self._flatten = flatten
        self._render = render or (lambda command, argument: f"{command}: {argument}".strip())
        self._render_chart = render_chart
        self._router = router
        self._authorize_resume = authorize_resume
        self._audit = audit
        self._nonces = nonce_store or NonceStore()
        self._alerts = alert_limiter or AlertLimiter()
        self._queue: deque[tuple[str, str, Mapping[str, Any] | None]] = deque(maxlen=max_queue)
        self._offset: int | None = None
        self._lock = Lock()

    @classmethod
    def from_owner_ids(
        cls, transport: TelegramTransport, owner_chat_ids: str, **kwargs: Any
    ) -> TelegramChannel:
        """Build from the comma-separated environment setting."""
        return cls(transport, owner_chat_ids.split(","), **kwargs)

    def poll_once(self, *, timeout: int = 30) -> int:
        """Process one bounded long-poll batch and return its update count."""
        updates = self._transport.get_updates(self._offset, timeout)
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = update_id + 1
            self.handle_update(update)
        self.flush()
        return len(updates)

    def run(self, *, stop: Callable[[], bool] | None = None, timeout: int = 30) -> None:
        """Run long-polling until ``stop`` returns true."""
        should_stop = stop or (lambda: False)
        consecutive_failures = 0
        while not should_stop():
            try:
                self.poll_once(timeout=timeout)
                consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001 - keep the channel alive
                consecutive_failures = min(consecutive_failures + 1, 5)
                delay = min(30.0, float(2 ** (consecutive_failures - 1)))
                LOGGER.warning(
                    "telegram poll failed (%s); backing off %.1fs",
                    type(exc).__name__,
                    delay,
                )
                if not should_stop():
                    time.sleep(delay)

    def handle_update(self, update: Mapping[str, Any]) -> None:
        message = update.get("message")
        if isinstance(message, Mapping):
            chat = message.get("chat")
            text = message.get("text")
            if isinstance(chat, Mapping) and isinstance(text, str):
                chat_id = self._chat_id(chat.get("id"))
                if chat_id is not None:
                    self.handle_message(chat_id, text)
        callback = update.get("callback_query")
        if isinstance(callback, Mapping):
            self._handle_callback(callback)

    def handle_message(self, chat_id: str, text: str) -> None:
        if chat_id not in self._owner_ids:
            LOGGER.info(
                "ignored non-owner telegram update",
                extra={"chat_hash": self._hash(chat_id)},
            )
            return
        command, argument = self._parse_command(text)
        if command == "/resume":
            if not self._resume_mode_is_safe(chat_id):
                return
            nonce = self._nonces.issue("resume")
            self._send(chat_id, "Confirm resume?", reply_markup=self._buttons(nonce, "resume"))
            return
        if command in {"/pause", "/stop", "/kill"}:
            self._apply_safe_control(chat_id, command)
            return
        if command == "/help":
            self._send(chat_id, "Supported commands: " + ", ".join(sorted(self.COMMANDS)))
            return
        if command == "/chart" and self._render_chart is not None:
            self._send_chart(chat_id, argument)
            return
        if command is None and self._router is not None:
            try:
                result = asyncio.run(self._router.route(text))
                reply = getattr(result, "reply", "The assistant is limited right now.")
            except Exception:
                LOGGER.warning(
                    "telegram chat routing failed", extra={"chat_hash": self._hash(chat_id)}
                )
                reply = "The assistant is limited right now; /status and /stop still work."
            self._send(chat_id, str(reply))
            return
        if command is None:
            return
        self._send(chat_id, self._render(command, argument))

    def _send_chart(self, chat_id: str, argument: str) -> None:
        try:
            renderer = self._render_chart
            if renderer is None:
                self._send(chat_id, self._render("/chart", argument))
                return
            image = renderer("/chart", argument)
            if not isinstance(image, bytes) or not image:
                raise ValueError("chart renderer returned no PNG")
            self._transport.send_photo(chat_id, image, caption=f"Chart: {argument}".strip())
        except Exception:
            LOGGER.warning(
                "telegram chart delivery failed", extra={"chat_hash": self._hash(chat_id)}
            )
            self._send(chat_id, "Chart is unavailable right now.")

    def send_alert(self, alert: Alert, *, chat_id: str | None = None) -> bool:
        """Queue a rate-limited alert; returns false when suppressed."""
        if not self._alerts.allow(alert.alert_key):
            return False
        for owner in [chat_id] if chat_id is not None else self._owner_ids:
            self._enqueue(owner, alert.text, None)
        return True

    def flush(self) -> None:
        """Retry queued outbound messages once; failures remain queued."""
        with self._lock:
            pending = list(self._queue)
            self._queue.clear()
        for chat_id, text, markup in pending:
            try:
                self._transport.send_message(chat_id, text, reply_markup=markup)
            except Exception:
                with self._lock:
                    self._queue.append((chat_id, text, markup))
                LOGGER.warning("telegram delivery failed", extra={"chat_hash": self._hash(chat_id)})

    def _apply_safe_control(self, chat_id: str, command: str) -> None:
        mode = {
            "/pause": ControlMode.PAUSE,
            "/stop": ControlMode.PAUSE,
            "/kill": ControlMode.KILL,
        }[command]
        previous = "unknown"
        if self._control_writer is not None:
            try:
                self._control_writer.set(mode)
            except Exception:
                self._audit_event(f"{command}_rejected", chat_id, previous, "unknown", None)
                self._send(chat_id, "Execution control is unavailable; execution remains halted.")
                return
        if command in {"/stop", "/kill"} and self._flatten is not None:
            try:
                flattened = self._flatten()
            except Exception:
                flattened = False
            if not flattened:
                self._send(chat_id, "Flattening is incomplete; execution remains halted.")
                self._audit_event(command, chat_id, previous, mode.value, None)
                return
        self._audit_event(command, chat_id, previous, mode.value, None)
        self._send(chat_id, f"{command} accepted; execution mode is {mode.value}.")

    def _handle_callback(self, callback: Mapping[str, Any]) -> None:
        callback_id = callback.get("id")
        data = callback.get("data")
        message = callback.get("message")
        chat = message.get("chat") if isinstance(message, Mapping) else None
        chat_id = self._chat_id(chat.get("id")) if isinstance(chat, Mapping) else None
        if not isinstance(callback_id, str) or not isinstance(data, str) or chat_id is None:
            return
        if chat_id not in self._owner_ids:
            LOGGER.info(
                "ignored non-owner telegram callback",
                extra={"chat_hash": self._hash(chat_id)},
            )
            return
        action, _, nonce = data.partition(":")
        if action == "cancel":
            self._nonces.consume_any(nonce)
            self._transport.answer_callback(callback_id, "Cancelled")
            return
        if action != "confirm_resume" or not self._nonces.consume(nonce, "resume"):
            self._transport.answer_callback(callback_id, "Expired or invalid confirmation")
            self._audit_event("resume_rejected", chat_id, "unknown", "unknown", nonce)
            return
        if not self._resume_mode_is_safe(chat_id, callback_id=callback_id):
            return
        if self._authorize_resume is None or not self._authorize_resume():
            self._transport.answer_callback(callback_id, "Owner authorization failed")
            self._audit_event("resume_rejected", chat_id, "pause", "unknown", nonce)
            return
        try:
            self._control_writer.set(ControlMode.RESUME)
        except Exception:
            self._transport.answer_callback(callback_id, "Execution control unavailable")
            return
        self._audit_event("resume", chat_id, "pause", ControlMode.RESUME.value, nonce)
        self._transport.answer_callback(callback_id, "Resume requested")
        self._send(chat_id, "Resume requested; reconciliation must succeed before entries resume.")

    def _resume_mode_is_safe(self, chat_id: str, *, callback_id: str | None = None) -> bool:
        """Reject resume when mode cannot be read or kill is latched."""
        if self._current_mode is None:
            message = "Resume unavailable; execution mode cannot be read."
        else:
            try:
                mode = self._current_mode()
            except Exception:
                mode = None
            if mode is ControlMode.KILL:
                message = "Resume unavailable; execution kill is latched."
            elif mode is None:
                message = "Resume unavailable; execution mode cannot be read."
            else:
                message = ""
        if message:
            if callback_id is None:
                self._send(chat_id, message)
            else:
                self._transport.answer_callback(callback_id, message)
            self._audit_event("resume_rejected", chat_id, "unknown", "unknown", None)
            return False
        return True

    def _send(
        self, chat_id: str, text: str, *, reply_markup: Mapping[str, Any] | None = None
    ) -> None:
        self._enqueue(chat_id, text, reply_markup)
        self.flush()

    def _enqueue(self, chat_id: str, text: str, markup: Mapping[str, Any] | None) -> None:
        with self._lock:
            self._queue.append((chat_id, text, markup))

    @staticmethod
    def _parse_command(text: str) -> tuple[str | None, str]:
        first, _, rest = text.strip().partition(" ")
        command = first.split("@", 1)[0].lower()
        return (command if command in TelegramChannel.COMMANDS else None, rest.strip())

    @staticmethod
    def _buttons(nonce: str, action: str) -> Mapping[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "Confirm", "callback_data": f"confirm_{action}:{nonce}"},
                    {"text": "Cancel", "callback_data": "cancel:" + nonce},
                ]
            ]
        }

    def _audit_event(
        self, event_type: str, chat_id: str, previous: str, new: str, nonce: str | None
    ) -> None:
        if self._audit is None:
            return
        from trader.core.audit import AuditEvent

        self._audit.append(
            AuditEvent(
                event_type=f"telegram_{event_type}",
                payload={
                    "source": "telegram",
                    "chat_id_hash": self._hash(chat_id),
                    "previous_mode": previous,
                    "new_mode": new,
                    "nonce_id": self._hash(nonce) if nonce else None,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        )

    @staticmethod
    def _chat_id(value: object) -> str | None:
        if isinstance(value, str | int):
            return str(value)
        return None

    @staticmethod
    def _hash(value: str | None) -> str:
        return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:16]


class TelegramBotTransport:
    """HTTP transport kept separate so the channel remains offline-testable."""

    def __init__(self, token: str, *, api_base: str = "https://api.telegram.org") -> None:
        if not token.strip():
            raise ValueError("Telegram bot token is required")
        self._token = token
        self._base = api_base.rstrip("/")

    def _request(self, method: str, payload: Mapping[str, Any]) -> Any:
        import httpx

        for logger_name in ("httpx", "httpcore"):
            logging.getLogger(logger_name).setLevel(logging.WARNING)
        try:
            response = httpx.post(
                f"{self._base}/bot{self._token}/{method}",
                json=dict(payload),
                timeout=40.0,
            )
        except Exception as exc:  # noqa: BLE001 - never expose request details
            LOGGER.warning(
                "telegram request failed method=%s status=%s telegram_error_code=%s (%s)",
                method,
                None,
                None,
                type(exc).__name__,
            )
            raise RuntimeError("Telegram API request failed") from None
        body = self._decode_response(method, response)
        if not body.get("ok"):
            raise RuntimeError("Telegram API request failed")
        return body.get("result")

    @staticmethod
    def _decode_response(method: str, response: Any) -> Mapping[str, Any]:
        """Decode a response and log only safe Telegram failure metadata."""
        status = getattr(response, "status_code", None)
        try:
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - response body is untrusted
            LOGGER.warning(
                "telegram request failed method=%s status=%s telegram_error_code=%s (%s)",
                method,
                status,
                None,
                type(exc).__name__,
            )
            raise RuntimeError("Telegram API request failed") from None
        if not isinstance(body, Mapping):
            LOGGER.warning(
                "telegram request failed method=%s status=%s "
                "telegram_error_code=%s (invalid_response)",
                method,
                status,
                None,
            )
            raise RuntimeError("Telegram API request failed")
        error_code = body.get("error_code")
        if not body.get("ok") or not (isinstance(status, int) and 200 <= status < 300):
            LOGGER.warning(
                "telegram request failed method=%s status=%s telegram_error_code=%s",
                method,
                status if isinstance(status, int) else None,
                error_code if isinstance(error_code, int) else None,
            )
            if not body.get("ok"):
                return body
            raise RuntimeError("Telegram API request failed")
        return body

    def get_updates(self, offset: int | None, timeout: int) -> Sequence[Mapping[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._request("getUpdates", payload)
        return result if isinstance(result, list) else []

    def send_message(
        self, chat_id: str, text: str, *, reply_markup: Mapping[str, Any] | None = None
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._request("sendMessage", payload)

    def send_photo(self, chat_id: str, photo: bytes, *, caption: str = "") -> None:
        import httpx

        for logger_name in ("httpx", "httpcore"):
            logging.getLogger(logger_name).setLevel(logging.WARNING)
        data: dict[str, str] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        try:
            response = httpx.post(
                f"{self._base}/bot{self._token}/sendPhoto",
                data=data,
                files={"photo": ("chart.png", photo, "image/png")},
                timeout=40.0,
            )
        except Exception as exc:  # noqa: BLE001 - never expose request details
            LOGGER.warning(
                "telegram request failed method=%s status=%s telegram_error_code=%s (%s)",
                "sendPhoto",
                None,
                None,
                type(exc).__name__,
            )
            raise RuntimeError("Telegram API request failed") from None
        body = self._decode_response("sendPhoto", response)
        if not body.get("ok"):
            raise RuntimeError("Telegram API request failed")

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        self._request("answerCallbackQuery", payload)

    def set_commands(self, commands: Sequence[Mapping[str, str]]) -> None:
        """Publish the bot menu without exposing the token or response body."""
        cleaned = [
            {"command": str(item["command"]), "description": str(item["description"])}
            for item in commands
        ]
        self._request("setMyCommands", {"commands": cleaned})
