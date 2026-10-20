from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from trader.core.logging import configure_logging
from trader.integrations.capital_com.stream import (
    CapitalComStreamClient,
    OhlcUpdate,
)


class FakeRest:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.ping_calls = 0
        self.renew_calls = 0
        self.token = "stale-cst"

    def _auth_headers(self) -> dict[str, str]:
        return {"CST": self.token, "X-SECURITY-TOKEN": f"{self.token}-security"}

    def ping(self) -> dict[str, str]:
        self.ping_calls += 1
        if self.ping_calls > 1:
            self.token = "fresh-cst"
        return {"status": "OK"}

    def renew_session(self) -> None:
        self.renew_calls += 1
        self.token = "renewed-cst"

    def iter_price_history(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(args)
        return [
            {
                "snapshotTimeUTC": "2026-10-03T10:01:00",
                "openPrice": {"bid": 1.0, "ask": 1.1},
                "highPrice": {"bid": 1.2, "ask": 1.3},
                "lowPrice": {"bid": 0.9, "ask": 1.0},
                "closePrice": {"bid": 1.1, "ask": 1.2},
            },
            {
                "snapshotTimeUTC": "2026-10-03T10:02:00",
                "openPrice": {"bid": 1.0, "ask": 1.1},
                "highPrice": {"bid": 1.2, "ask": 1.3},
                "lowPrice": {"bid": 0.9, "ask": 1.0},
                "closePrice": {"bid": 1.1, "ask": 1.2},
            },
        ]


class FakeSocket:
    def __init__(self, events: list[Any], on_message: Callable[[str], None] | None = None) -> None:
        self.events = iter(events)
        self.sent: list[str] = []
        self.on_message = on_message

    def __enter__(self) -> FakeSocket:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def send(self, data: str) -> None:
        self.sent.append(data)

    def recv(self, *, timeout: float) -> str:
        item = next(self.events)
        if isinstance(item, Exception):
            raise item
        if self.on_message is not None:
            self.on_message(item)
        return item


def event(timestamp_ms: int, side: str = "bid") -> str:
    return json.dumps(
        {
            "destination": "ohlc.event",
            "payload": {
                "epic": "US100",
                "priceType": side,
                "t": timestamp_ms,
                "o": 1.0,
                "h": 1.2,
                "l": 0.9,
                "c": 1.1,
            },
        }
    )


def subscribe_reply(
    *, correlation_id: str = "1", status: str = "OK", subscription_status: str = "PROCESSED"
) -> str:
    return json.dumps(
        {
            "destination": "OHLCMarketData.subscribe",
            "correlationId": correlation_id,
            "status": status,
            "payload": {"subscriptions": {"US100:MINUTE:classic": subscription_status}},
        }
    )


def ping_reply(*, correlation_id: str = "2", status: str = "OK") -> str:
    return json.dumps({"destination": "ping", "correlationId": correlation_id, "status": status})


def test_stream_parser_emits_typed_bid_update() -> None:
    parsed = CapitalComStreamClient._parse_stream_update(json.loads(event(1791021600000)))
    assert parsed is not None
    assert parsed.epic == "US100"
    assert parsed.price_type == "bid"
    assert parsed.timestamp.tzinfo == UTC
    assert parsed.source == "capital_ws"


def test_rest_parser_treats_naive_snapshot_time_utc() -> None:
    candle = FakeRest().iter_price_history("US100")[0]
    parsed = CapitalComStreamClient._parse_rest_candle("US100", candle)
    assert parsed[0].timestamp == datetime(2026, 10, 3, 10, 1, tzinfo=UTC)


def test_backfill_includes_the_watermark_minute_for_rest_reconciliation() -> None:
    rest = FakeRest()
    updates: list[OhlcUpdate] = []
    stamp = datetime(2026, 10, 3, 10, 1, tzinfo=UTC)
    client = CapitalComStreamClient(
        rest,
        ["US100"],
        updates.append,
        clock=lambda: datetime(2026, 10, 3, 10, 3, tzinfo=UTC),
    )
    client.seed_watermarks({("US100", side): stamp for side in ("bid", "ask")})

    client._backfill()

    assert any(
        item.source == "capital_rest" and item.timestamp == stamp and item.price_type == "bid"
        for item in updates
    )


def test_backfill_invokes_one_batch_callback_per_epic() -> None:
    rest = FakeRest()
    batches: list[list[OhlcUpdate]] = []
    stamp = datetime(2026, 10, 3, 10, 1, tzinfo=UTC)
    client = CapitalComStreamClient(
        rest,
        ["US100"],
        lambda _event: None,
        on_backfill_batch=lambda batch: batches.append(list(batch)),
        clock=lambda: datetime(2026, 10, 3, 10, 3, tzinfo=UTC),
    )
    client.seed_watermarks({("US100", side): stamp for side in ("bid", "ask")})

    assert client._backfill() == 4
    assert len(batches) == 1
    assert len(batches[0]) == 4


def test_session_renewal_is_bounded_per_minute() -> None:
    rest = FakeRest()
    client = CapitalComStreamClient(rest, ["US100"], lambda _event: None)

    assert [client._renew_session() for _ in range(4)] == [True, True, True, False]
    assert rest.renew_calls == 3


def test_stream_reconnects_resubscribes_and_backfills_lagging_side() -> None:
    rest = FakeRest()
    updates: list[OhlcUpdate] = []
    reconnects: list[tuple[int, str]] = []
    stop = threading.Event()
    sockets = [
        FakeSocket(
            [
                subscribe_reply(),
                ping_reply(),
                event(1791021720000, "bid"),
                event(1791021660000, "ask"),
                OSError("forced disconnect"),
            ]
        ),
        FakeSocket(
            [subscribe_reply(), ping_reply(), event(1791021780000, "bid")],
            on_message=lambda _raw: stop.set(),
        ),
    ]
    connect_calls = 0

    def factory(*_args: Any, **_kwargs: Any) -> FakeSocket:
        nonlocal connect_calls
        socket = sockets[connect_calls]
        connect_calls += 1
        return socket

    client = CapitalComStreamClient(
        rest,
        ["US100"],
        updates.append,
        on_reconnect=lambda count, reason: reconnects.append((count, reason)),
        connection_factory=factory,
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.02,
        clock=lambda: datetime(2026, 10, 3, 10, 3, tzinfo=UTC),
        sleep=lambda _delay: None,
    )
    client.run_forever(stop)

    assert connect_calls == 2
    assert rest.ping_calls == 2
    assert len(rest.calls) == 1
    assert rest.calls[0][2] == datetime(2026, 10, 3, 10, 1, tzinfo=UTC)
    assert any(
        update.source == "capital_rest"
        and update.price_type == "ask"
        and update.timestamp == datetime(2026, 10, 3, 10, 2, tzinfo=UTC)
        for update in updates
    )
    assert client.reconnect_count == 1
    assert reconnects == [(1, "OSError")]
    assert client.ping_reply_ok is True
    requests = [json.loads(message) for socket in sockets for message in socket.sent]
    assert all(
        request["destination"] in {"OHLCMarketData.subscribe", "ping"} for request in requests
    )
    assert sockets[1].sent
    assert json.loads(sockets[1].sent[0])["cst"] == "fresh-cst"


def test_reconnect_logs_context_without_payload_or_credentials(caplog) -> None:
    rest = FakeRest()
    stop = threading.Event()
    first = FakeSocket([subscribe_reply(), ping_reply(), event(1791021600000), OSError("forced")])
    second = FakeSocket(
        [subscribe_reply(), ping_reply(), event(1791021660000)],
        on_message=lambda _raw: stop.set(),
    )
    sockets = iter([first, second])
    caplog.set_level(logging.INFO, logger="trader.integrations.capital_com.stream")
    client = CapitalComStreamClient(
        rest,
        ["US100"],
        lambda _update: None,
        connection_factory=lambda *_args, **_kwargs: next(sockets),
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.02,
        sleep=lambda _delay: None,
        clock=lambda: datetime(2026, 10, 3, 10, 3, tzinfo=UTC),
    )

    client.run_forever(stop)

    records = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO and "Capital.com stream reconnect:" in record.getMessage()
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "exception=OSError" in message
    assert "seconds_since_ping_reply=0.000" in message
    assert "seconds_since_tick=0.000" in message
    assert "connection_age_seconds=0.000" in message
    assert "forced" not in message
    assert any("Capital.com stream attempt:" in record.getMessage() for record in caplog.records)


def test_connect_failure_reports_reconnect_and_recovers() -> None:
    rest = FakeRest()
    updates: list[OhlcUpdate] = []
    reconnects: list[tuple[int, str]] = []
    stop = threading.Event()

    def stop_on_event(raw: str) -> None:
        if json.loads(raw).get("destination") == "ohlc.event":
            stop.set()

    recovered = FakeSocket(
        [subscribe_reply(), ping_reply(), event(1791021600000)],
        on_message=stop_on_event,
    )
    attempts = iter([OSError("connect refused"), recovered])

    def connect(*_args: object, **_kwargs: object) -> FakeSocket:
        attempt = next(attempts)
        if isinstance(attempt, Exception):
            raise attempt
        return attempt

    client = CapitalComStreamClient(
        rest,
        ["US100"],
        updates.append,
        on_reconnect=lambda count, reason: reconnects.append((count, reason)),
        connection_factory=connect,
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.02,
        sleep=lambda _delay: None,
    )
    client.run_forever(stop)

    assert reconnects == [(1, "OSError")]
    assert client.reconnect_count == 1
    assert client.ping_reply_ok is True
    assert updates and updates[0].source == "capital_ws"


def test_no_data_timeout_reports_short_reason_and_retries() -> None:
    rest = FakeRest()
    reconnects: list[tuple[int, str]] = []
    stop = threading.Event()
    current = [datetime(2026, 10, 3, 10, 0, tzinfo=UTC)]

    class IdleSocket(FakeSocket):
        raw = 0

        def recv(self, *, timeout: float) -> str:
            self.raw += 1
            if self.raw <= 2:
                return super().recv(timeout=timeout)
            current[0] += timedelta(seconds=6)
            raise TimeoutError

    socket = IdleSocket([subscribe_reply(), ping_reply()])
    client = CapitalComStreamClient(
        rest,
        ["US100"],
        lambda _update: None,
        on_reconnect=lambda count, reason: reconnects.append((count, reason)),
        connection_factory=lambda *_args, **_kwargs: socket,
        data_timeout_seconds=5,
        ping_interval=timedelta(days=30),
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.02,
        clock=lambda: current[0],
        sleep=lambda _delay: stop.set(),
    )

    client.run_forever(stop)

    assert reconnects == [(1, "no_data_timeout")]
    assert client.reconnect_count == 1
    assert client.ping_reply_ok is False


def test_no_data_timeout_is_suppressed_when_market_is_closed() -> None:
    rest = FakeRest()
    reconnects: list[tuple[int, str]] = []
    stop = threading.Event()
    current = [datetime(2026, 10, 4, 10, 0, tzinfo=UTC)]

    class ClosedMarketSocket(FakeSocket):
        calls = 0
        raw = 0

        def recv(self, *, timeout: float) -> str:
            self.raw += 1
            if self.raw <= 2:
                return super().recv(timeout=timeout)
            self.calls += 1
            current[0] += timedelta(seconds=6)
            if self.calls >= 3:
                stop.set()
            raise TimeoutError

    socket = ClosedMarketSocket([subscribe_reply(), ping_reply()])
    client = CapitalComStreamClient(
        rest,
        ["US100"],
        lambda _update: None,
        on_reconnect=lambda count, reason: reconnects.append((count, reason)),
        connection_factory=lambda *_args, **_kwargs: socket,
        data_timeout_seconds=5,
        ping_interval=timedelta(days=30),
        data_expected=lambda: False,
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.02,
        clock=lambda: current[0],
        sleep=lambda _delay: None,
    )

    client.run_forever(stop)

    assert reconnects == []
    assert client.reconnect_count == 0


def test_watchdog_starts_at_market_open_not_at_last_tick() -> None:
    rest = FakeRest()
    reconnects: list[tuple[int, str]] = []
    stop = threading.Event()
    current = [datetime(2026, 10, 2, 21, 0, tzinfo=UTC)]
    expected = [False]

    class WeekendSocket(FakeSocket):
        calls = 0
        raw = 0

        def recv(self, *, timeout: float) -> str:
            self.raw += 1
            if self.raw <= 2:
                return super().recv(timeout=timeout)
            self.calls += 1
            if self.calls == 2:
                current[0] += timedelta(days=2)
                expected[0] = True
            else:
                current[0] += timedelta(seconds=2)
            if self.calls >= 4:
                stop.set()
            raise TimeoutError

    socket = WeekendSocket([subscribe_reply(), ping_reply()])
    client = CapitalComStreamClient(
        rest,
        ["US100"],
        lambda _update: None,
        on_reconnect=lambda count, reason: reconnects.append((count, reason)),
        connection_factory=lambda *_args, **_kwargs: socket,
        data_timeout_seconds=5,
        ping_interval=timedelta(days=30),
        data_expected=lambda: expected[0],
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.02,
        clock=lambda: current[0],
        sleep=lambda _delay: None,
    )

    client.run_forever(stop)

    assert reconnects == []


@pytest.mark.parametrize(
    "failed_reply",
    [subscribe_reply(status="ERROR"), subscribe_reply(subscription_status="ERROR")],
)
def test_stale_subscribe_reply_retries_with_fresh_rest_ping(failed_reply: str) -> None:
    rest = FakeRest()
    stop = threading.Event()
    first = FakeSocket([failed_reply])
    second = FakeSocket(
        [subscribe_reply(), ping_reply(), event(1791021600000)],
        on_message=lambda _raw: stop.set(),
    )
    sockets = iter([first, second])

    client = CapitalComStreamClient(
        rest,
        ["US100"],
        lambda _update: None,
        connection_factory=lambda *_args, **_kwargs: next(sockets),
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.02,
        sleep=lambda _delay: None,
    )
    client.run_forever(stop)

    assert rest.ping_calls == 2
    assert json.loads(second.sent[0])["cst"] == "fresh-cst"


@pytest.mark.parametrize("reply", [TimeoutError(), ping_reply(correlation_id="2", status="ERROR")])
def test_missing_or_non_ok_ping_reply_reconnects(reply: Any) -> None:
    rest = FakeRest()
    stop = threading.Event()
    socket = FakeSocket([subscribe_reply(), reply])
    client = CapitalComStreamClient(
        rest,
        ["US100"],
        lambda _update: None,
        connection_factory=lambda *_args, **_kwargs: socket,
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.02,
        sleep=lambda _delay: stop.set(),
    )

    client.run_forever(stop)

    assert client.ping_reply_ok is False
    assert any(json.loads(message)["destination"] == "ping" for message in socket.sent)


def test_stream_propagates_consumer_exception_and_logs_fixed_value_error(caplog) -> None:
    rest = FakeRest()
    socket = FakeSocket([subscribe_reply(), ping_reply(), event(1791021600000)])
    client = CapitalComStreamClient(
        rest,
        ["US100"],
        lambda _update: (_ for _ in ()).throw(ValueError("consumer failed")),
        connection_factory=lambda *_args, **_kwargs: socket,
    )

    with pytest.raises(ValueError, match="consumer failed"):
        client.run_forever(threading.Event())
    assert "consumer failed" in caplog.text
    assert "Capital.com stream consumer failed (ValueError)" in caplog.text


def test_websockets_debug_logging_is_disabled() -> None:
    logger = logging.getLogger("websockets")
    original_level = logger.level
    try:
        logger.setLevel(logging.DEBUG)
        configure_logging()
        assert logger.level == logging.WARNING
        assert not logger.isEnabledFor(logging.DEBUG)
    finally:
        logger.setLevel(original_level)


def test_stream_limits_epics_to_forty() -> None:
    with pytest.raises(ValueError, match="40"):
        CapitalComStreamClient(FakeRest(), [f"E{i}" for i in range(41)], lambda _event: None)


def test_stream_sends_keepalive_ping_before_ten_minutes() -> None:
    rest = FakeRest()
    stop = threading.Event()

    def stop_on_periodic_ping(raw: str) -> None:
        message = json.loads(raw)
        if message.get("destination") == "ping" and message.get("correlationId") == "3":
            stop.set()

    socket = FakeSocket(
        [subscribe_reply(), ping_reply(), TimeoutError(), ping_reply(correlation_id="3")],
        on_message=stop_on_periodic_ping,
    )
    current = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)

    def clock() -> datetime:
        nonlocal current
        value = current
        current += timedelta(minutes=9)
        return value

    client = CapitalComStreamClient(
        rest,
        ["US100"],
        lambda _update: None,
        connection_factory=lambda *_args, **_kwargs: socket,
        clock=clock,
    )
    client.run_forever(stop)

    messages = [json.loads(message) for message in socket.sent]
    assert sum(message["destination"] == "ping" for message in messages) == 2
