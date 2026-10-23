"""Capital.com OHLC market-data WebSocket with reconnect and REST gap fill."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

from trader.core.diagnostics import bounded_text, exception_chain
from trader.integrations.capital_com.client import CapitalComClient, CapitalComError, Resolution

_LOG = logging.getLogger(__name__)
logging.getLogger("websockets").setLevel(logging.WARNING)
_STREAM_URL = "wss://api-streaming-capital.backend-capital.com/connect"
# Capital.com closes quiet sockets in roughly two to three minutes in the
# demo environment. Keep the JSON keepalive comfortably inside that window;
# the websocket protocol ping is disabled because the API requires a JSON
# ``ping`` request with the current session tokens.
_PING_INTERVAL = timedelta(seconds=45)
_REPLY_TIMEOUT_SECONDS = 30.0
_DEFAULT_DATA_TIMEOUT_SECONDS = 1800.0
_SESSION_RENEWAL_LIMIT = 3
_SESSION_RENEWAL_WINDOW_SECONDS = 60.0


class StreamProtocolError(RuntimeError):
    """The streaming service did not acknowledge a request as expected."""


class _ConsumerCallbackFailure(Exception):
    def __init__(self, original: Exception) -> None:
        self.original = original


@dataclass(frozen=True, slots=True)
class OhlcUpdate:
    """One bid or ask OHLC update from Capital.com."""

    epic: str
    timestamp: datetime
    price_type: str
    open: float
    high: float
    low: float
    close: float
    source: str


ConnectionFactory = Callable[..., ClientConnection]
UpdateHandler = Callable[[OhlcUpdate], None]
BatchUpdateHandler = Callable[[Iterable[OhlcUpdate]], None]
ReconnectHandler = Callable[[int, str], None]
DataExpected = Callable[[], bool]


class CapitalComStreamClient:
    """Subscribe to minute OHLC, validate liveness, and REST-backfill reconnect gaps."""

    def __init__(
        self,
        rest_client: CapitalComClient,
        epics: Iterable[str],
        on_update: UpdateHandler,
        *,
        on_backfill_batch: BatchUpdateHandler | None = None,
        on_reconnect: ReconnectHandler | None = None,
        connection_factory: ConnectionFactory = connect,
        reconnect_min_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        ping_interval: timedelta = _PING_INTERVAL,
        data_timeout_seconds: float = _DEFAULT_DATA_TIMEOUT_SECONDS,
        data_expected: DataExpected | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        normalized = tuple(dict.fromkeys(epic.strip() for epic in epics if epic.strip()))
        if not normalized:
            raise ValueError("at least one epic is required")
        if len(normalized) > 40:
            raise ValueError("Capital.com supports at most 40 subscribed epics")
        if reconnect_min_seconds <= 0 or reconnect_max_seconds < reconnect_min_seconds:
            raise ValueError("invalid reconnect backoff bounds")
        if data_timeout_seconds <= 0:
            raise ValueError("data_timeout_seconds must be positive")
        self._rest = rest_client
        self._epics = normalized
        self._on_update = on_update
        self._on_backfill_batch = on_backfill_batch
        self._on_reconnect = on_reconnect
        self._connect = connection_factory
        self._reconnect_min = reconnect_min_seconds
        self._reconnect_max = reconnect_max_seconds
        self._ping_interval = ping_interval
        self._data_timeout = timedelta(seconds=data_timeout_seconds)
        self._data_expected = data_expected
        self._clock = clock
        self._sleep = sleep
        self._last_seen: dict[tuple[str, str], datetime] = {}
        self._watermark_lock = threading.RLock()
        self._correlation = 0
        self._reconnect_count = 0
        self._ping_reply_ok: bool | None = None
        self._reply_status = "missing"
        self._last_data_at: datetime | None = None
        self._last_ping_reply_at: datetime | None = None
        self._connection_started_at: datetime | None = None
        self._attempt_id = 0
        self._session_renewals: list[float] = []
        self._frame_minute: datetime | None = None
        self._frame_counts: dict[str, int] = {}
        self._last_frame_type = "none"
        self._last_frame_at: datetime | None = None
        self._last_boundary_minute: datetime | None = None
        self._next_lag_sample: float | None = None

    @property
    def reconnect_count(self) -> int:
        """Number of failed connection or established-stream attempts."""
        return self._reconnect_count

    @property
    def ping_reply_ok(self) -> bool | None:
        """Whether the most recent WebSocket keepalive received an OK reply."""
        return self._ping_reply_ok

    def seed_watermarks(self, watermarks: Mapping[tuple[str, str], datetime]) -> None:
        """Seed persisted side timestamps so startup REST backfill resumes there."""
        for (epic, side), stamp in watermarks.items():
            if epic not in self._epics or side not in {"bid", "ask"} or stamp.tzinfo is None:
                raise ValueError("invalid persisted OHLC watermark")
            with self._watermark_lock:
                self._last_seen[(epic, side)] = stamp.astimezone(UTC)

    def _message(self, destination: str, payload: Mapping[str, Any] | None = None) -> str:
        self._correlation += 1
        headers = self._rest._auth_headers()
        message: dict[str, Any] = {
            "destination": destination,
            "correlationId": str(self._correlation),
            "cst": headers["CST"],
            "securityToken": headers["X-SECURITY-TOKEN"],
        }
        if payload is not None:
            message["payload"] = dict(payload)
        return json.dumps(message, separators=(",", ":"))

    def _send_request(
        self,
        socket: ClientConnection,
        destination: str,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        message = self._message(destination, payload)
        correlation_id = str(json.loads(message)["correlationId"])
        socket.send(message)
        return correlation_id

    def _await_reply(
        self,
        socket: ClientConnection,
        correlation_id: str,
        destination: str,
        *,
        timeout: float = _REPLY_TIMEOUT_SECONDS,
    ) -> Mapping[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StreamProtocolError(f"Capital.com {destination} reply timed out")
            try:
                raw = socket.recv(timeout=remaining)
            except TimeoutError:
                raise StreamProtocolError(f"Capital.com {destination} reply timed out") from None
            self._record_frame(raw)
            try:
                message = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                _LOG.warning("Ignoring malformed Capital.com WebSocket reply")
                continue
            if not isinstance(message, Mapping):
                continue
            if message.get("destination") == "ohlc.event":
                self._handle(raw)
                continue
            if str(message.get("correlationId", "")) != correlation_id:
                continue
            status = message.get("status")
            self._reply_status = status if isinstance(status, str) else "missing"
            if status != "OK" or message.get("destination") != destination:
                _LOG.warning(
                    "Capital.com WebSocket request failed: destination=%s status=%s",
                    destination,
                    status if isinstance(status, str) else "missing",
                )
                raise StreamProtocolError(f"Capital.com {destination} reply was not OK")
            return message

    def _subscribe(self, socket: ClientConnection) -> None:
        correlation_id = self._send_request(
            socket,
            "OHLCMarketData.subscribe",
            {"epics": list(self._epics), "resolutions": ["MINUTE"], "type": "classic"},
        )
        try:
            reply = self._await_reply(socket, correlation_id, "OHLCMarketData.subscribe")
        except StreamProtocolError:
            _LOG.warning(
                "Capital.com OHLC subscription failed: epics=%s status=%s",
                self._epics,
                self._reply_status,
            )
            raise
        payload = reply.get("payload")
        subscriptions = payload.get("subscriptions") if isinstance(payload, Mapping) else None
        if not isinstance(subscriptions, Mapping):
            raise StreamProtocolError("Capital.com subscribe reply is missing subscriptions")
        expected = {f"{epic}:MINUTE:classic" for epic in self._epics}
        confirmed = {key for key, status in subscriptions.items() if status == "PROCESSED"}
        missing = sorted(expected - confirmed)
        if missing:
            _LOG.warning(
                "Capital.com OHLC subscription incomplete: epics=%s status=OK",
                [item.split(":", maxsplit=1)[0] for item in missing],
            )
            raise StreamProtocolError("Capital.com did not confirm every OHLC subscription")

    def _ping(self, socket: ClientConnection) -> None:
        self._ping_reply_ok = False
        correlation_id = self._send_request(socket, "ping")
        self._await_reply(socket, correlation_id, "ping")
        self._ping_reply_ok = True
        self._last_ping_reply_at = self._clock().astimezone(UTC)

    def _record_reconnect(self, exc: BaseException, reason: str) -> None:
        """Log one bounded, secret-free reconnect observation per failed attempt."""
        now = self._clock().astimezone(UTC)

        def age_seconds(stamp: datetime | None) -> str:
            if stamp is None:
                return "unknown"
            return f"{max(0.0, (now - stamp).total_seconds()):.3f}"

        close_code = getattr(exc, "code", None)
        if not isinstance(close_code, int):
            close_code = "unknown"
        close_reason = getattr(exc, "reason", None)
        if not isinstance(close_reason, str) or not close_reason:
            close_reason = "unknown"
        else:
            close_reason = close_reason[:160]
        connection_age = age_seconds(self._connection_started_at)
        frame_at = self._last_frame_at.isoformat() if self._last_frame_at is not None else "none"
        _LOG.info(
            "Capital.com stream reconnect: exception=%s reason=%s close_code=%s "
            "close_reason=%s seconds_since_ping_reply=%s seconds_since_tick=%s "
            "connection_age_seconds=%s last_frame_type=%s last_frame_at=%s "
            "exception_chain=%s",
            type(exc).__name__,
            reason,
            close_code,
            close_reason,
            age_seconds(self._last_ping_reply_at),
            age_seconds(self._last_data_at),
            connection_age,
            self._last_frame_type,
            frame_at,
            exception_chain(exc),
        )

    def _record_frame(self, raw: str | bytes) -> None:
        """Record frame type/count metadata without logging payloads."""
        stamp = datetime.now(UTC)
        minute = stamp.replace(second=0, microsecond=0)
        if self._frame_minute is not None and minute != self._frame_minute:
            self._flush_frame_counts()
        if self._frame_minute != minute:
            self._frame_minute = minute
            self._frame_counts = {}
        message_type = "malformed"
        try:
            message = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            message = None
        if isinstance(message, Mapping):
            destination = message.get("destination")
            if isinstance(destination, str) and destination:
                message_type = bounded_text(destination, limit=80)
            else:
                message_type = "unknown"
        self._frame_counts[message_type] = self._frame_counts.get(message_type, 0) + 1
        self._last_frame_type = message_type
        self._last_frame_at = stamp

    def _flush_frame_counts(self) -> None:
        """Emit one aggregate line for the completed frame minute."""
        if self._frame_minute is None or not self._frame_counts:
            return
        for message_type, count in sorted(self._frame_counts.items()):
            _LOG.info(
                "Capital.com stream frame counts: minute=%s message_type=%s count=%s",
                self._frame_minute.isoformat(),
                message_type,
                count,
            )
        self._frame_counts = {}

    def _record_boundary(self, started_at: datetime, ended_at: datetime, elapsed: float) -> None:
        """Log the receive block that crossed each UTC minute boundary."""
        boundary = ended_at.replace(second=0, microsecond=0)
        if self._last_boundary_minute is None:
            self._last_boundary_minute = boundary
            return
        if boundary <= self._last_boundary_minute:
            return
        self._last_boundary_minute = boundary
        _LOG.info(
            "Capital.com stream boundary: minute=%s reader_block_start=%s "
            "reader_block_end=%s reader_block_duration_seconds=%.3f thread=%s",
            boundary.isoformat(),
            started_at.isoformat(),
            ended_at.isoformat(),
            elapsed,
            threading.current_thread().name,
        )

    def _sample_loop_lag(self, now: float) -> None:
        """Sample scheduler delay once per second and log only material lag."""
        if self._next_lag_sample is None:
            self._next_lag_sample = now + 1.0
            return
        if now < self._next_lag_sample:
            return
        lag = now - self._next_lag_sample
        while self._next_lag_sample <= now:
            self._next_lag_sample += 1.0
        if lag > 0.250:
            _LOG.warning(
                "Capital.com stream event-loop lag: lag_seconds=%.3f thread=%s",
                lag,
                threading.current_thread().name,
            )

    @staticmethod
    def _number(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("OHLC price must be numeric")
        result = float(value)
        if not math.isfinite(result) or result <= 0:
            raise ValueError("OHLC price must be finite and positive")
        return result

    @classmethod
    def _parse_stream_update(cls, message: Mapping[str, Any]) -> OhlcUpdate | None:
        if message.get("destination") != "ohlc.event":
            return None
        payload = message.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("invalid OHLC event")
        epic = payload.get("epic")
        price_type = payload.get("priceType")
        timestamp_ms = payload.get("t")
        if not isinstance(epic, str) or price_type not in ("bid", "ask"):
            raise ValueError("invalid OHLC event identity")
        if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int):
            raise ValueError("invalid OHLC event timestamp")
        stamp = datetime.fromtimestamp(timestamp_ms / 1000, UTC)
        return OhlcUpdate(
            epic=epic,
            timestamp=stamp,
            price_type=price_type,
            open=cls._number(payload.get("o")),
            high=cls._number(payload.get("h")),
            low=cls._number(payload.get("l")),
            close=cls._number(payload.get("c")),
            source="capital_ws",
        )

    @classmethod
    def _parse_rest_candle(cls, epic: str, candle: Mapping[str, Any]) -> list[OhlcUpdate]:
        stamp_text = candle.get("snapshotTimeUTC")
        if not isinstance(stamp_text, str):
            raise ValueError("REST candle is missing snapshotTimeUTC")
        stamp = datetime.fromisoformat(stamp_text.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        updates = []
        for side in ("bid", "ask"):
            values = [
                candle.get(key, {}).get(side) if isinstance(candle.get(key), Mapping) else None
                for key in ("openPrice", "highPrice", "lowPrice", "closePrice")
            ]
            if all(value is not None for value in values):
                updates.append(
                    OhlcUpdate(
                        epic=epic,
                        timestamp=stamp.astimezone(UTC),
                        price_type=side,
                        open=cls._number(values[0]),
                        high=cls._number(values[1]),
                        low=cls._number(values[2]),
                        close=cls._number(values[3]),
                        source="capital_rest",
                    )
                )
        if not updates:
            raise ValueError("REST candle has no bid or ask OHLC values")
        return updates

    def _emit(self, update: OhlcUpdate) -> None:
        try:
            self._on_update(update)
        except Exception as exc:
            if isinstance(exc, ValueError):
                _LOG.error(
                    "Capital.com stream consumer failed (%s): %s",
                    type(exc).__name__,
                    str(exc)[:160],
                )
            else:
                _LOG.error("Capital.com stream consumer failed (%s)", type(exc).__name__)
            raise _ConsumerCallbackFailure(exc) from exc

    def _emit_backfill_batch(self, updates: list[OhlcUpdate]) -> None:
        if not updates:
            return
        callback = self._on_backfill_batch
        if callback is None:
            for update in updates:
                self._emit(update)
            return
        try:
            callback(tuple(updates))
        except Exception as exc:
            if isinstance(exc, ValueError):
                _LOG.error(
                    "Capital.com stream consumer failed (%s): %s",
                    type(exc).__name__,
                    str(exc)[:160],
                )
            else:
                _LOG.error("Capital.com stream consumer failed (%s)", type(exc).__name__)
            raise _ConsumerCallbackFailure(exc) from exc

    def _backfill(self) -> int:
        emitted_count = 0
        for epic in self._epics:
            started = time.monotonic()
            with self._watermark_lock:
                seen = {
                    side: stamp for (item, side), stamp in self._last_seen.items() if item == epic
                }
            if not seen:
                _LOG.info(
                    "Capital.com backfill start: epic=%s start=none end=%s pass=0",
                    epic,
                    self._clock().astimezone(UTC).isoformat(),
                )
                _LOG.info(
                    "Capital.com backfill end: epic=%s start=none end=%s candles=0 "
                    "passes=0 elapsed_seconds=%.3f",
                    epic,
                    self._clock().astimezone(UTC).isoformat(),
                    time.monotonic() - started,
                )
                continue
            passes = 0
            epic_count = 0
            last_start = min(seen.values()).replace(second=0, microsecond=0)
            last_end = self._clock().astimezone(UTC)
            while passes < 2:
                with self._watermark_lock:
                    for (item, side), stamp in self._last_seen.items():
                        if item == epic and (side not in seen or stamp > seen[side]):
                            seen[side] = stamp
                end = self._clock().astimezone(UTC)
                start = min(seen.values()).replace(second=0, microsecond=0)
                last_start, last_end = start, end
                _LOG.info(
                    "Capital.com backfill start: epic=%s start=%s end=%s pass=%s",
                    epic,
                    start.isoformat(),
                    end.isoformat(),
                    passes + 1,
                )
                if start >= end - timedelta(minutes=1):
                    break
                emitted: dict[str, set[datetime]] = {"bid": set(), "ask": set()}
                batch: list[OhlcUpdate] = []
                before = dict(seen)
                for candle in self._rest.iter_price_history(
                    epic, Resolution.MINUTE, start, end, max_pages=100
                ):
                    for update in self._parse_rest_candle(epic, candle):
                        watermark = seen.get(update.price_type)
                        if watermark is not None and update.timestamp < watermark:
                            continue
                        if update.timestamp in emitted[update.price_type]:
                            continue
                        batch.append(update)
                        emitted[update.price_type].add(update.timestamp)
                        if watermark is None or update.timestamp > watermark:
                            seen[update.price_type] = update.timestamp
                            with self._watermark_lock:
                                key = (epic, update.price_type)
                                current = self._last_seen.get(key)
                                if current is None or update.timestamp > current:
                                    self._last_seen[key] = update.timestamp
                self._emit_backfill_batch(batch)
                epic_count += len(batch)
                emitted_count += len(batch)
                passes += 1
                progressed = any(seen.get(side) != before.get(side) for side in seen)
                if (
                    not batch
                    or not progressed
                    or min(seen.values()) >= (self._clock().astimezone(UTC) - timedelta(minutes=2))
                ):
                    break
            _LOG.info(
                "Capital.com backfill end: epic=%s start=%s end=%s candles=%s "
                "passes=%s elapsed_seconds=%.3f",
                epic,
                last_start.isoformat(),
                last_end.isoformat(),
                epic_count,
                passes,
                time.monotonic() - started,
            )
        return emitted_count

    def _renew_session(self) -> bool:
        """Refresh REST credentials at most three times per minute."""
        renew = getattr(self._rest, "renew_session", None)
        if not callable(renew):
            return False
        now = time.monotonic()
        self._session_renewals = [
            stamp
            for stamp in self._session_renewals
            if now - stamp < _SESSION_RENEWAL_WINDOW_SECONDS
        ]
        if len(self._session_renewals) >= _SESSION_RENEWAL_LIMIT:
            _LOG.warning("Capital.com session renewal deferred: max attempts per minute reached")
            return False
        self._session_renewals.append(now)
        try:
            renew()
        except Exception as exc:  # noqa: BLE001 - keep reconnect loop alive
            _LOG.warning("Capital.com session renewal failed (%s)", type(exc).__name__)
            return False
        _LOG.info("Capital.com session renewed before reconnect")
        return True

    def _attempt_log(
        self,
        *,
        attempt_id: int,
        rest_ping_ok: bool,
        socket_connected: bool,
        subscribe_ok: bool,
        ping_ok: bool,
        backfill_started: bool,
        backfill_completed: bool,
        backfill_updates: int,
        exception: BaseException | None,
    ) -> None:
        _LOG.info(
            "Capital.com stream attempt: id=%s rest_ping_ok=%s socket_connected=%s "
            "subscribe_ok=%s ping_ok=%s backfill_started=%s backfill_completed=%s "
            "backfill_updates=%s exception=%s",
            attempt_id,
            rest_ping_ok,
            socket_connected,
            subscribe_ok,
            ping_ok,
            backfill_started,
            backfill_completed,
            backfill_updates,
            type(exception).__name__ if exception is not None else "none",
        )

    def _handle(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            _LOG.warning("Ignoring malformed Capital.com WebSocket message")
            return
        if not isinstance(message, Mapping):
            return
        update = self._parse_stream_update(message)
        if update is None or update.epic not in self._epics:
            return
        self._emit(update)
        self._last_data_at = self._clock().astimezone(UTC)
        key = (update.epic, update.price_type)
        with self._watermark_lock:
            previous = self._last_seen.get(key)
            if previous is None or update.timestamp > previous:
                self._last_seen[key] = update.timestamp

    def run_forever(self, stop_event: threading.Event) -> None:
        """Run until stopped; validate replies, reconnect, and REST-backfill gaps."""
        backoff = self._reconnect_min
        while not stop_event.is_set():
            self._attempt_id += 1
            attempt_id = self._attempt_id
            rest_ping_ok = False
            socket_connected = False
            subscribe_ok = False
            ping_ok = False
            backfill_started = False
            backfill_completed = False
            backfill_updates = 0
            backfill_thread: threading.Thread | None = None
            backfill_state: dict[str, Any] = {"updates": 0, "error": None}
            attempt_error: BaseException | None = None
            try:
                self._rest.ping()
                rest_ping_ok = True
                self._connection_started_at = None
                with self._connect(
                    _STREAM_URL, open_timeout=10, close_timeout=2, ping_interval=None
                ) as socket:
                    self._connection_started_at = self._clock().astimezone(UTC)
                    socket_connected = True
                    self._correlation = 0
                    self._reply_status = "missing"
                    self._subscribe(socket)
                    subscribe_ok = True
                    self._ping(socket)
                    ping_ok = True

                    def run_backfill(state: dict[str, Any] = backfill_state) -> None:
                        try:
                            state["updates"] = self._backfill()
                            state["completed"] = True
                        except BaseException as exc:  # noqa: BLE001 - hand back to stream loop
                            state["error"] = exc

                    backfill_started = True
                    backfill_thread = threading.Thread(
                        target=run_backfill,
                        name="capital-data-backfill",
                        daemon=True,
                    )
                    backfill_thread.start()
                    self._last_data_at = self._clock().astimezone(UTC)
                    backoff = self._reconnect_min
                    last_ping = self._clock()
                    was_expected = self._is_data_expected()
                    while not stop_event.is_set():
                        backfill_error = backfill_state.get("error")
                        if backfill_error is not None:
                            raise backfill_error
                        backfill_updates = int(backfill_state.get("updates", 0))
                        backfill_completed = bool(backfill_state.get("completed", False))
                        receive_started_at = datetime.now(UTC)
                        receive_started = time.monotonic()
                        try:
                            raw = socket.recv(timeout=1)
                        except TimeoutError:
                            raw = None
                        receive_ended_at = datetime.now(UTC)
                        self._record_boundary(
                            receive_started_at,
                            receive_ended_at,
                            time.monotonic() - receive_started,
                        )
                        self._sample_loop_lag(time.monotonic())
                        if raw is not None:
                            self._record_frame(raw)
                            self._handle(raw)
                        now = self._clock()
                        expected = self._is_data_expected()
                        if expected and not was_expected:
                            # Start the watchdog at market open, not at the last pre-close tick.
                            self._last_data_at = now.astimezone(UTC)
                        was_expected = expected
                        if (
                            not stop_event.is_set()
                            and self._last_data_at is not None
                            and expected
                            and now.astimezone(UTC) - self._last_data_at >= self._data_timeout
                        ):
                            raise StreamProtocolError("Capital.com OHLC no-data timeout")
                        if not stop_event.is_set() and now - last_ping >= self._ping_interval:
                            self._ping(socket)
                            last_ping = now
            except _ConsumerCallbackFailure as exc:
                attempt_error = exc.original
                self._ping_reply_ok = False
                self._reconnect_count += 1
                reason = type(exc.original).__name__
                self._record_reconnect(exc.original, reason)
                if isinstance(exc.original, CapitalComError):
                    self._renew_session()
                if not stop_event.is_set() and self._on_reconnect is not None:
                    self._on_reconnect(self._reconnect_count, reason)
                raise exc.original from None
            except (ConnectionClosed, OSError, TimeoutError, StreamProtocolError) as exc:
                attempt_error = exc
                self._ping_reply_ok = False
                self._reconnect_count += 1
                if stop_event.is_set():
                    break
                reason = "no_data_timeout" if "no-data timeout" in str(exc) else type(exc).__name__
                self._record_reconnect(exc, reason)
                _LOG.warning("Capital.com WebSocket disconnected (%s)", type(exc).__name__)
                if self._on_reconnect is not None:
                    self._on_reconnect(self._reconnect_count, reason)
            except Exception as exc:
                attempt_error = exc
                self._ping_reply_ok = False
                self._reconnect_count += 1
                if stop_event.is_set():
                    break
                self._record_reconnect(exc, type(exc).__name__)
                _LOG.warning("Capital.com WebSocket reconnecting (%s)", type(exc).__name__)
                if isinstance(exc, CapitalComError):
                    self._renew_session()
                if self._on_reconnect is not None:
                    self._on_reconnect(self._reconnect_count, type(exc).__name__)
            finally:
                if backfill_thread is not None:
                    backfill_thread.join(timeout=2)
                    backfill_updates = int(backfill_state.get("updates", 0))
                    backfill_completed = bool(backfill_state.get("completed", False))
                self._attempt_log(
                    attempt_id=attempt_id,
                    rest_ping_ok=rest_ping_ok,
                    socket_connected=socket_connected,
                    subscribe_ok=subscribe_ok,
                    ping_ok=ping_ok,
                    backfill_started=backfill_started,
                    backfill_completed=backfill_completed,
                    backfill_updates=backfill_updates,
                    exception=attempt_error,
                )
            if not stop_event.is_set():
                self._sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max)

    def _is_data_expected(self) -> bool:
        """Return whether market hours currently require stream prices."""
        if self._data_expected is None:
            return True
        try:
            return self._data_expected()
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            _LOG.warning("Capital.com market expectation unavailable (%s)", type(exc).__name__)
            return False
