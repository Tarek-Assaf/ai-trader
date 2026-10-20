from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq

from trader.data.recorder import CapitalDataRecorder, ParquetCandleStore
from trader.integrations.capital_com.stream import OhlcUpdate


class CountingStore:
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, OhlcUpdate]]] = []

    def upsert_many(self, items, *, on_reject=None) -> int:
        batch = list(items)
        self.calls.append(batch)
        return len(batch)


def _update(
    minute: datetime, side: str, *, source: str = "capital_ws", close: float = 100.0
) -> OhlcUpdate:
    return OhlcUpdate(
        epic="US100",
        timestamp=minute,
        price_type=side,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        source=source,
    )


def test_partial_minute_waits_for_rollover_and_writes_spread_once_closed(tmp_path: Path) -> None:
    current = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    recorder = CapitalDataRecorder(
        ParquetCandleStore(tmp_path / "lake"),
        {"US100": "NAS100"},
        tmp_path / "status.json",
        clock=lambda: current,
    )
    recorder.on_update(_update(current, "bid", close=100.0))
    recorder.on_update(_update(current, "ask", close=101.5))
    assert not list((tmp_path / "lake").rglob("*.parquet"))

    recorder.on_update(_update(current + timedelta(minutes=1), "bid", close=100.2))

    output = tmp_path / "lake" / "NAS100" / "1m_capital" / "2026" / "10.parquet"
    rows = pq.read_table(output).to_pylist()
    closed = [row for row in rows if row["ts_utc"] == current]
    assert {row["side"] for row in closed} == {"bid", "ask"}
    assert {row["spread_close"] for row in closed} == {1.5}
    assert {row["lineage_tag"] for row in closed} == {"capital_com_1m_recorder_v1"}
    assert {row["source"] for row in closed} == {"capital_ws"}


def test_backfill_batch_commits_once_to_the_store(tmp_path: Path) -> None:
    current = datetime(2026, 10, 3, 12, 3, tzinfo=UTC)
    store = CountingStore()
    recorder = CapitalDataRecorder(
        store,
        {"US100": "NAS100"},
        tmp_path / "status.json",
        clock=lambda: current,
    )

    recorder.on_backfill_batch(
        [
            _update(current - timedelta(minutes=2), "bid", source="capital_rest"),
            _update(current - timedelta(minutes=2), "ask", source="capital_rest"),
            _update(current - timedelta(minutes=1), "bid", source="capital_rest"),
            _update(current - timedelta(minutes=1), "ask", source="capital_rest"),
        ]
    )

    assert len(store.calls) == 1
    assert len(store.calls[0]) == 4


def test_upsert_is_idempotent_and_rest_wins_for_same_minute(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path / "lake")
    minute = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    ws = _update(minute, "bid", close=100.0)
    rest = _update(minute, "bid", source="capital_rest", close=100.5)
    store.upsert_many([("NAS100", rest)])
    store.upsert_many([("NAS100", ws), ("NAS100", rest)])

    output = tmp_path / "lake" / "NAS100" / "1m_capital" / "2026" / "10.parquet"
    rows = pq.read_table(output).to_pylist()
    assert len(rows) == 1
    assert rows[0]["close"] == 100.5
    assert rows[0]["source"] == "capital_rest"
    assert rows[0]["lineage_tag"] == "capital_com_1m_recorder_v1"


def test_rest_reconciliation_wins_over_later_websocket_update(tmp_path: Path) -> None:
    current = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    recorder = CapitalDataRecorder(
        ParquetCandleStore(tmp_path / "lake"),
        {"US100": "NAS100"},
        tmp_path / "status.json",
        clock=lambda: current,
    )
    recorder.on_update(_update(current, "bid", source="capital_rest", close=100.0))
    recorder.on_update(_update(current, "bid", source="capital_ws", close=100.5))
    recorder.on_update(_update(current + timedelta(minutes=1), "bid", close=101.0))

    output = tmp_path / "lake" / "NAS100" / "1m_capital" / "2026" / "10.parquet"
    rows = pq.read_table(output).to_pylist()
    closed = next(row for row in rows if row["ts_utc"] == current and row["side"] == "bid")
    assert closed["close"] == 100.0
    assert closed["source"] == "capital_rest"


def test_startup_watermarks_and_liveness_use_ping_and_market_status(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path / "lake")
    minute = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    store.upsert_many([("NAS100", _update(minute, "bid"))])
    recorder = CapitalDataRecorder(
        ParquetCandleStore(tmp_path / "lake"),
        {"US100": "NAS100"},
        tmp_path / "status.json",
    )
    recorder.update_liveness(ping_reply_ok=True, market_status="CLOSED")
    recorder.on_reconnect(2, "ConnectionClosed")

    value = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert recorder.watermarks() == {
        ("US100", "bid"): minute,
        ("US100", "ask"): minute,
    }
    assert value["ping_reply_ok"] is True
    assert value["market_status"] == "CLOSED"
    assert value["ticks_received"] == 0
    assert value["last_tick_at"] is None
    assert value["reconnect_count"] == 2
    assert value["last_reconnect_reason"] == "ConnectionClosed"


def test_stream_failure_is_degraded_and_secret_free(tmp_path: Path) -> None:
    recorder = CapitalDataRecorder(
        ParquetCandleStore(tmp_path / "lake"),
        {"US100": "NAS100"},
        tmp_path / "status.json",
    )

    recorder.on_reconnect(1, "no_data_timeout")

    value = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert value["status"] == "degraded"
    assert value["reconnect_count"] == 1
    assert value["last_reconnect_reason"] == "no_data_timeout"
    assert all(secret not in json.dumps(value) for secret in ("CST", "securityToken"))


def test_closed_market_suppresses_stale_tick_alarm(tmp_path: Path) -> None:
    now = [datetime(2026, 10, 3, 12, 0, tzinfo=UTC)]
    recorder = CapitalDataRecorder(
        ParquetCandleStore(tmp_path / "lake"),
        {"US100": "NAS100"},
        tmp_path / "status.json",
        clock=lambda: now[0],
    )
    now[0] += timedelta(minutes=10)
    recorder.update_liveness(
        ping_reply_ok=True,
        market_status="CLOSED",
        expected_open_by_epic={"US100": False},
        market_status_source="broker",
        stale_after_seconds_by_epic={"US100": 180},
    )

    value = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert value["status"] == "healthy"
    assert value["expected_open"] is False
    assert value["stale_epics"] == []


def test_tradeable_market_with_stale_ticks_degrades_recorder(tmp_path: Path) -> None:
    now = [datetime(2026, 10, 3, 12, 0, tzinfo=UTC)]
    recorder = CapitalDataRecorder(
        ParquetCandleStore(tmp_path / "lake"),
        {"US100": "NAS100"},
        tmp_path / "status.json",
        clock=lambda: now[0],
    )
    recorder.update_liveness(
        ping_reply_ok=True,
        market_status="TRADEABLE",
        expected_open_by_epic={"US100": True},
        market_status_source="broker",
        stale_after_seconds_by_epic={"US100": 180},
    )
    assert recorder.last_tick_at("US100") is None

    now[0] += timedelta(seconds=181)
    recorder.update_liveness(
        ping_reply_ok=True,
        market_status="TRADEABLE",
        expected_open_by_epic={"US100": True},
        market_status_source="broker",
        stale_after_seconds_by_epic={"US100": 180},
    )

    value = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert value["status"] == "degraded"
    assert value["stale_epics"] == ["US100"]
    assert value["ticks_received"] == 0
    assert value["last_tick_at"] is None


def test_feed_stall_alerts_are_bounded_and_recovery_is_reported(tmp_path: Path) -> None:
    now = [datetime(2026, 10, 3, 12, 0, tzinfo=UTC)]
    alerts: list[str] = []
    recorder = CapitalDataRecorder(
        ParquetCandleStore(tmp_path / "lake"),
        {"US100": "NAS100"},
        tmp_path / "status.json",
        clock=lambda: now[0],
        on_alert=alerts.append,
    )

    now[0] += timedelta(minutes=6)
    recorder.update_liveness(
        ping_reply_ok=True,
        market_status="TRADEABLE",
        expected_open_by_epic={"US100": True},
    )
    assert len(alerts) == 1
    assert "feed stalled" in alerts[0]
    value = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert value["feed_stalled"] is True
    assert value["minutes_since_last_bar"] == 6.0

    now[0] += timedelta(minutes=29)
    recorder.update_liveness(
        ping_reply_ok=True,
        market_status="TRADEABLE",
        expected_open_by_epic={"US100": True},
    )
    assert len(alerts) == 1

    now[0] += timedelta(minutes=1)
    recorder.update_liveness(
        ping_reply_ok=True,
        market_status="TRADEABLE",
        expected_open_by_epic={"US100": True},
    )
    assert len(alerts) == 2
    assert "feed stalled" in alerts[1]

    bar = now[0].replace(second=0, microsecond=0)
    recorder.on_update(_update(bar, "bid"))
    recorder.on_update(_update(bar, "ask", close=101.0))
    recorder.on_update(_update(bar + timedelta(minutes=1), "bid"))
    assert any("feed recovered" in alert for alert in alerts)


def test_recorder_health_counts_received_ticks(tmp_path: Path) -> None:
    current = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    recorder = CapitalDataRecorder(
        ParquetCandleStore(tmp_path / "lake"),
        {"US100": "NAS100"},
        tmp_path / "status.json",
        clock=lambda: current,
    )

    recorder.on_update(_update(current, "bid"))
    value = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))

    assert value["ticks_received"] == 1
    assert value["last_tick_at"] == current.isoformat()


def test_bad_spread_is_quarantined_without_stopping_recorder(tmp_path: Path) -> None:
    current = datetime(2026, 10, 3, 12, 3, tzinfo=UTC)
    recorder = CapitalDataRecorder(
        ParquetCandleStore(tmp_path / "lake"),
        {"US100": "NAS100"},
        tmp_path / "status.json",
        clock=lambda: current,
    )
    minute = current - timedelta(minutes=2)

    store = ParquetCandleStore(tmp_path / "lake")
    store.upsert_many(
        [
            ("NAS100", _update(minute, "bid", source="capital_rest", close=100.0)),
            ("NAS100", _update(minute, "ask", source="capital_rest", close=99.0)),
            (
                "NAS100",
                _update(minute + timedelta(minutes=1), "bid", source="capital_rest", close=101.0),
            ),
            (
                "NAS100",
                _update(minute + timedelta(minutes=1), "ask", source="capital_rest", close=102.0),
            ),
        ],
        on_reject=recorder._record_rejection,
    )
    recorder._write_status()

    output = tmp_path / "lake" / "NAS100" / "1m_capital" / "2026" / "10.parquet"
    rows = pq.read_table(output).to_pylist()
    assert {(row["ts_utc"], row["side"]) for row in rows} == {
        (minute, "bid"),
        (minute + timedelta(minutes=1), "bid"),
        (minute + timedelta(minutes=1), "ask"),
    }
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["rejected_candles"] == 1
    assert status["last_reject_reason"] == "ask_close_below_bid_close"

