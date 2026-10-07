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

from trader.integrations.capital_com.client import CapitalComClient, Resolution

_LOG = logging.getLogger(__name__)
logging.getLogger("websockets").setLevel(logging.WARNING)
_STREAM_URL = "wss://api-streaming-capital.backend-capital.com/connect"
_PING_INTERVAL = timedelta(minutes=8)
_REPLY_TIMEOUT_SECONDS = 30.0
_DEFAULT_DATA_TIMEOUT_SECONDS = 1800.0


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
        self._correlation = 0
        self._reconnect_count = 0
        self._ping_reply_ok: bool | None = None
        self._reply_status = "missing"
        self._last_data_at: datetime | None = None

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
            _LOG.error("Capital.com stream consumer failed (%s)", type(exc).__name__)
            raise _ConsumerCallbackFailure(exc) from exc

    def _backfill(self) -> None:
        end = self._clock().astimezone(UTC)
        for epic in self._epics:
            seen = {side: stamp for (item, side), stamp in self._last_seen.items() if item == epic}
            if not seen:
                continue
            start = min(seen.values()).replace(second=0, microsecond=0)
            if start >= end:
                continue
            emitted: dict[str, set[datetime]] = {"bid": set(), "ask": set()}
            for candle in self._rest.iter_price_history(
                epic, Resolution.MINUTE, start, end, max_pages=100
            ):
                for update in self._parse_rest_candle(epic, candle):
                    watermark = seen.get(update.price_type)
                    if watermark is not None and update.timestamp < watermark:
                        continue
                    if update.timestamp in emitted[update.price_type]:
                        continue
                    self._emit(update)
                    emitted[update.price_type].add(update.timestamp)
                    if watermark is None or update.timestamp > watermark:
                        seen[update.price_type] = update.timestamp
                        self._last_seen[(epic, update.price_type)] = update.timestamp

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
        previous = self._last_seen.get(key)
        if previous is None or update.timestamp > previous:
            self._last_seen[key] = update.timestamp

    def run_forever(self, stop_event: threading.Event) -> None:
        """Run until stopped; validate replies, reconnect, and REST-backfill gaps."""
        backoff = self._reconnect_min
        while not stop_event.is_set():
            try:
                self._rest.ping()
                with self._connect(
                    _STREAM_URL, open_timeout=10, close_timeout=2, ping_interval=None
                ) as socket:
                    self._correlation = 0
                    self._reply_status = "missing"
                    self._subscribe(socket)
                    self._ping(socket)
                    self._backfill()
                    self._last_data_at = self._clock().astimezone(UTC)
                    backoff = self._reconnect_min
                    last_ping = self._clock()
                    while not stop_event.is_set():
                        try:
                            raw = socket.recv(timeout=1)
                        except TimeoutError:
                            raw = None
                        if raw is not None:
                            self._handle(raw)
                        now = self._clock()
                        if (
                            not stop_event.is_set()
                            and self._last_data_at is not None
                            and self._is_data_expected()
                            and now.astimezone(UTC) - self._last_data_at >= self._data_timeout
                        ):
                            raise StreamProtocolError("Capital.com OHLC no-data timeout")
                        if not stop_event.is_set() and now - last_ping >= self._ping_interval:
                            self._ping(socket)
                            last_ping = now
            except _ConsumerCallbackFailure as exc:
                raise exc.original from None
            except (ConnectionClosed, OSError, TimeoutError, StreamProtocolError) as exc:
                self._ping_reply_ok = False
                self._reconnect_count += 1
                if stop_event.is_set():
                    break
                _LOG.warning("Capital.com WebSocket disconnected (%s)", type(exc).__name__)
                if self._on_reconnect is not None:
                    reason = (
                        "no_data_timeout"
                        if "no-data timeout" in str(exc)
                        else type(exc).__name__
                    )
                    self._on_reconnect(self._reconnect_count, reason)
            except Exception as exc:
                self._ping_reply_ok = False
                self._reconnect_count += 1
                if stop_event.is_set():
                    break
                _LOG.warning("Capital.com WebSocket reconnecting (%s)", type(exc).__name__)
                if self._on_reconnect is not None:
                    self._on_reconnect(self._reconnect_count, type(exc).__name__)
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
