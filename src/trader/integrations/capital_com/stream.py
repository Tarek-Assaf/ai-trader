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
_STREAM_URL = "wss://api-streaming-capital.backend-capital.com/connect"
_PING_INTERVAL = timedelta(minutes=8)


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


class CapitalComStreamClient:
    """Subscribe to configured minute OHLC and fill reconnect gaps via REST.

    The WebSocket session uses the authenticated REST client's current tokens.
    Callbacks receive normalized updates; this class never logs auth messages or
    market payloads.
    """

    def __init__(
        self,
        rest_client: CapitalComClient,
        epics: Iterable[str],
        on_update: UpdateHandler,
        *,
        connection_factory: ConnectionFactory = connect,
        reconnect_min_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        ping_interval: timedelta = _PING_INTERVAL,
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
        self._rest = rest_client
        self._epics = normalized
        self._on_update = on_update
        self._connect = connection_factory
        self._reconnect_min = reconnect_min_seconds
        self._reconnect_max = reconnect_max_seconds
        self._ping_interval = ping_interval
        self._clock = clock
        self._sleep = sleep
        self._last_seen: dict[tuple[str, str], datetime] = {}
        self._correlation = 0

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

    def _subscribe(self, socket: ClientConnection) -> None:
        socket.send(
            self._message(
                "OHLCMarketData.subscribe",
                {"epics": list(self._epics), "resolutions": ["MINUTE"], "type": "classic"},
            )
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
            raise ValueError("REST candle timestamp must include a timezone")
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

    def _backfill(self) -> None:
        end = self._clock().astimezone(UTC)
        for epic in self._epics:
            seen = [stamp for (item, _), stamp in self._last_seen.items() if item == epic]
            if not seen:
                continue
            # Re-fetch the last observed minute: it may have been incomplete at disconnect.
            start = min(seen).replace(second=0, microsecond=0)
            if start >= end:
                continue
            for candle in self._rest.iter_price_history(
                epic, Resolution.MINUTE, start, end, max_pages=100
            ):
                for update in self._parse_rest_candle(epic, candle):
                    self._on_update(update)
                    key = (epic, update.price_type)
                    previous = self._last_seen.get(key)
                    if previous is None or update.timestamp > previous:
                        self._last_seen[key] = update.timestamp

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
        self._on_update(update)
        key = (update.epic, update.price_type)
        previous = self._last_seen.get(key)
        if previous is None or update.timestamp > previous:
            self._last_seen[key] = update.timestamp

    def run_forever(self, stop_event: threading.Event) -> None:
        """Run until stopped; reconnect, re-subscribe, and REST-backfill gaps."""
        backoff = self._reconnect_min
        while not stop_event.is_set():
            try:
                with self._connect(
                    _STREAM_URL, open_timeout=10, close_timeout=2, ping_interval=None
                ) as socket:
                    self._subscribe(socket)
                    self._backfill()
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
                        if not stop_event.is_set() and now - last_ping >= self._ping_interval:
                            socket.send(self._message("ping"))
                            last_ping = now
            except (ConnectionClosed, OSError, TimeoutError) as exc:
                if stop_event.is_set():
                    break
                _LOG.warning("Capital.com WebSocket disconnected (%s)", type(exc).__name__)
            except Exception as exc:
                if stop_event.is_set():
                    break
                _LOG.warning("Capital.com WebSocket reconnecting (%s)", type(exc).__name__)
            if not stop_event.is_set():
                self._sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max)
