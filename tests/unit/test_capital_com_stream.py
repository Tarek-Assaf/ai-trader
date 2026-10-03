from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from trader.integrations.capital_com.stream import CapitalComStreamClient, OhlcUpdate


class FakeRest:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def _auth_headers(self) -> dict[str, str]:
        return {"CST": "secret-cst", "X-SECURITY-TOKEN": "secret-token"}

    def iter_price_history(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(args)
        return [
            {
                "snapshotTimeUTC": "2026-10-03T10:01:00Z",
                "openPrice": {"bid": 1.0, "ask": 1.1},
                "highPrice": {"bid": 1.2, "ask": 1.3},
                "lowPrice": {"bid": 0.9, "ask": 1.0},
                "closePrice": {"bid": 1.1, "ask": 1.2},
            }
        ]


class FakeSocket:
    def __init__(self, events: list[Any]) -> None:
        self.events = iter(events)
        self.sent: list[str] = []

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
        return item


def event(timestamp_ms: int) -> str:
    return json.dumps(
        {
            "destination": "ohlc.event",
            "payload": {
                "epic": "US100",
                "priceType": "bid",
                "t": timestamp_ms,
                "o": 1.0,
                "h": 1.2,
                "l": 0.9,
                "c": 1.1,
            },
        }
    )


def test_stream_parser_emits_typed_bid_update() -> None:
    parsed = CapitalComStreamClient._parse_stream_update(json.loads(event(1791021600000)))
    assert parsed is not None
    assert parsed.epic == "US100"
    assert parsed.price_type == "bid"
    assert parsed.timestamp.tzinfo == UTC
    assert parsed.source == "capital_ws"


def test_stream_reconnects_resubscribes_and_backfills_gap() -> None:
    rest = FakeRest()
    updates: list[OhlcUpdate] = []
    stop = threading.Event()
    sockets = [
        FakeSocket([event(1791021600000), RuntimeError("forced disconnect")]),
        FakeSocket([event(1791021720000)]),
    ]
    connect_calls = 0

    def factory(*_args: Any, **_kwargs: Any) -> FakeSocket:
        nonlocal connect_calls
        socket = sockets[connect_calls]
        connect_calls += 1
        if connect_calls == 2:
            original_recv = socket.recv

            def receive(*, timeout: float) -> str:
                value = original_recv(timeout=timeout)
                stop.set()
                return value

            socket.recv = receive  # type: ignore[method-assign]
        return socket

    client = CapitalComStreamClient(
        rest,
        ["US100"],
        updates.append,
        connection_factory=factory,
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.02,
        clock=lambda: datetime(2026, 10, 3, 10, 3, tzinfo=UTC),
        sleep=lambda _delay: None,
    )
    client.run_forever(stop)

    assert connect_calls == 2
    assert len(rest.calls) == 1
    assert rest.calls[0][2] == datetime(2026, 10, 3, 10, 0, tzinfo=UTC)
    assert any(update.source == "capital_rest" and update.price_type == "ask" for update in updates)
    requests = [json.loads(message) for socket in sockets for message in socket.sent]
    assert all(request["destination"] == "OHLCMarketData.subscribe" for request in requests)
    assert all("secret-cst" not in repr(update) for update in updates)


def test_stream_limits_epics_to_forty() -> None:
    try:
        CapitalComStreamClient(FakeRest(), [f"E{i}" for i in range(41)], lambda _event: None)
    except ValueError as exc:
        assert "40" in str(exc)
    else:
        raise AssertionError("expected the subscription limit to be enforced")


def test_stream_sends_keepalive_ping_before_ten_minutes() -> None:
    rest = FakeRest()
    stop = threading.Event()
    class StopAfterTwoTimeouts(FakeSocket):
        def __init__(self) -> None:
            super().__init__([])
            self.calls = 0

        def recv(self, *, timeout: float) -> str:
            self.calls += 1
            if self.calls == 2:
                stop.set()
            raise TimeoutError

    socket = StopAfterTwoTimeouts()
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
    assert any(message["destination"] == "ping" for message in messages)
