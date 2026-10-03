"""End-to-end recorder coverage for stream disconnect gap reconciliation."""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from trader.data.recorder import CapitalDataRecorder, ParquetCandleStore
from trader.integrations.capital_com.stream import CapitalComStreamClient, OhlcUpdate


class _Rest:
    def __init__(self, candles: list[dict[str, Any]]) -> None:
        self.candles = candles

    def ping(self) -> dict[str, str]:
        return {"status": "OK"}

    def _auth_headers(self) -> dict[str, str]:
        return {"CST": "test-cst", "X-SECURITY-TOKEN": "test-security-token"}

    def iter_price_history(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return self.candles


class _Socket:
    def __init__(
        self, messages: list[str | Exception], on_message: Callable[[str], None] | None = None
    ) -> None:
        self._messages = iter(messages)
        self._on_message = on_message

    def __enter__(self) -> _Socket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def send(self, _message: str) -> None:
        return None

    def recv(self, *, timeout: float) -> str:
        del timeout
        message = next(self._messages)
        if isinstance(message, Exception):
            raise message
        if self._on_message is not None:
            self._on_message(message)
        return message


def _reply(destination: str, correlation_id: str, *, subscriptions: bool = False) -> str:
    payload = {"subscriptions": {"US100:MINUTE:classic": "PROCESSED"}} if subscriptions else {}
    return json.dumps(
        {
            "destination": destination,
            "correlationId": correlation_id,
            "status": "OK",
            "payload": payload,
        }
    )


def _stream_event(stamp: datetime, side: str) -> str:
    return json.dumps(
        {
            "destination": "ohlc.event",
            "payload": {
                "epic": "US100",
                "priceType": side,
                "t": int(stamp.timestamp() * 1000),
                "o": 100.0,
                "h": 102.0,
                "l": 99.0,
                "c": 101.0,
            },
        }
    )


def _rest_candle(stamp: datetime) -> dict[str, Any]:
    return {
        "snapshotTimeUTC": stamp.isoformat(),
        "openPrice": {"bid": 100.0, "ask": 101.0},
        "highPrice": {"bid": 102.0, "ask": 103.0},
        "lowPrice": {"bid": 99.0, "ask": 100.0},
        "closePrice": {"bid": 101.0, "ask": 102.0},
    }


def _bar(stamp: datetime, side: str) -> OhlcUpdate:
    price = 100.0 if side == "bid" else 101.0
    return OhlcUpdate("US100", stamp, side, price, price + 1, price - 1, price, "capital_rest")


def test_recorder_persists_each_side_once_across_disconnect_and_rest_backfill(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)
    current = start + timedelta(minutes=3)
    minutes = [start + timedelta(minutes=offset) for offset in range(3)]
    store = ParquetCandleStore(tmp_path / "lake")
    store.upsert_many([("NAS100", _bar(start, side)) for side in ("bid", "ask")])
    recorder = CapitalDataRecorder(
        store,
        {"US100": "NAS100"},
        tmp_path / "recorder-status.json",
        clock=lambda: current,
    )
    stop = threading.Event()
    first_socket = _Socket(
        [
            _reply("OHLCMarketData.subscribe", "1", subscriptions=True),
            _reply("ping", "2"),
            _stream_event(minutes[1], "bid"),
            _stream_event(minutes[1], "ask"),
            OSError("forced disconnect"),
        ]
    )

    def stop_on_current_event(raw: str) -> None:
        message = json.loads(raw)
        if message.get("destination") == "ohlc.event" and message["payload"].get("t") == int(
            current.timestamp() * 1000
        ):
            stop.set()

    second_socket = _Socket(
        [
            _reply("OHLCMarketData.subscribe", "1", subscriptions=True),
            _reply("ping", "2"),
            _stream_event(current, "bid"),
        ],
        on_message=stop_on_current_event,
    )
    sockets = iter([first_socket, second_socket])
    rest = _Rest([_rest_candle(stamp) for stamp in minutes])
    client = CapitalComStreamClient(
        rest,  # type: ignore[arg-type]
        ["US100"],
        recorder.on_update,
        on_reconnect=recorder.on_reconnect,
        connection_factory=lambda *_args, **_kwargs: next(sockets),  # type: ignore[arg-type]
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.02,
        clock=lambda: current,
        sleep=lambda _delay: None,
    )
    client.seed_watermarks(recorder.watermarks())

    client.run_forever(stop)

    output = tmp_path / "lake" / "NAS100" / "1m_capital" / "2026" / "10.parquet"
    rows = pq.read_table(output).to_pylist()
    counts = Counter((row["ts_utc"], row["side"]) for row in rows)
    expected = {(stamp, side) for stamp in minutes for side in ("bid", "ask")}
    assert set(counts) == expected
    assert all(counts[key] == 1 for key in expected)
    assert len(rows) == len(expected)
    assert all(
        row["source"] == "capital_rest"
        for row in rows
        if row["ts_utc"] in minutes[1:]
    )
    assert client.reconnect_count == 1
