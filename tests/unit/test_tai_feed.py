from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trader.experiments.tai_feed import TaiLakeTailer, read_closed_lake_minutes
from trader.experiments.tai_wiring import TaiMinuteBar, ThreeMinuteBarBuilder


def _write_fixture(
    path: Path,
    start: datetime,
    count: int,
    *,
    missing_ask: set[int] | None = None,
) -> None:
    pq = pytest.importorskip("pyarrow.parquet")
    import pyarrow as pa

    rows: list[dict[str, object]] = []
    for offset in range(count):
        stamp = start + timedelta(minutes=offset)
        for side, base in (("bid", 100.0), ("ask", 101.0)):
            if side == "ask" and offset in (missing_ask or set()):
                continue
            rows.append(
                {
                    "epic": "NSXUSD",
                    "ts_utc": stamp,
                    "side": side,
                    "open": base,
                    "high": base + 1.0,
                    "low": base - 1.0,
                    "close": base + 0.5,
                }
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


class Consumer:
    def __init__(self) -> None:
        self.minutes: list[TaiMinuteBar] = []
        self.bars: list[datetime] = []
        self.stale_count = 0
        self.recovered_count = 0
        self.builder = ThreeMinuteBarBuilder("NSXUSD")

    def on_minute_bar(self, minute: TaiMinuteBar) -> None:
        self.minutes.append(minute)
        bar = self.builder.add(minute)
        if bar is not None:
            self.bars.append(bar.at)

    def mark_feed_stale(self) -> None:
        self.stale_count += 1

    def mark_feed_healthy(self) -> None:
        self.recovered_count += 1


def test_tailer_feeds_each_closed_minute_once_and_builds_20_bars(tmp_path: Path) -> None:
    start = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    _write_fixture(tmp_path / "NSXUSD" / "1m_capital" / "2026" / "10.parquet", start, 60)
    consumer = Consumer()
    tailer = TaiLakeTailer(
        tmp_path,
        "NSXUSD",
        consumer,
        clock=lambda: start + timedelta(hours=1),
    )

    assert tailer.poll_once(start + timedelta(hours=1)) == 60
    assert len(consumer.minutes) == 60
    assert len(consumer.bars) == 20
    assert tailer.poll_once(start + timedelta(hours=1, seconds=20)) == 0
    assert len(consumer.minutes) == 60


def test_tailer_skips_incomplete_minutes_and_restart_seed(tmp_path: Path) -> None:
    start = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    _write_fixture(
        tmp_path / "NSXUSD" / "1m_capital" / "2026" / "10.parquet",
        start,
        6,
        missing_ask={2},
    )
    consumer = Consumer()
    tailer = TaiLakeTailer(tmp_path, "NSXUSD", consumer, seed_after=start + timedelta(minutes=2))

    assert tailer.poll_once(start + timedelta(hours=1)) == 3
    assert [item.at for item in consumer.minutes] == [
        start + timedelta(minutes=3),
        start + timedelta(minutes=4),
        start + timedelta(minutes=5),
    ]


def test_read_with_after_returns_only_recent_minutes(tmp_path: Path) -> None:
    """The incremental read matches the full read for the same recent window."""
    start = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    _write_fixture(tmp_path / "NSXUSD" / "1m_capital" / "2026" / "10.parquet", start, 120)
    before = start + timedelta(hours=3)
    cutoff = start + timedelta(minutes=100)

    full = read_closed_lake_minutes(tmp_path, "NSXUSD", before=before)
    recent = read_closed_lake_minutes(tmp_path, "NSXUSD", before=before, after=cutoff)

    assert len(full) == 120
    assert [item.at for item in recent] == [item.at for item in full if item.at >= cutoff]
    assert len(recent) == 20


def test_tailer_reads_only_recent_rows_after_the_first_poll(tmp_path: Path) -> None:
    """After the seed, each poll still feeds every new minute exactly once."""
    start = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    path = tmp_path / "NSXUSD" / "1m_capital" / "2026" / "10.parquet"
    _write_fixture(path, start, 60)
    consumer = Consumer()
    tailer = TaiLakeTailer(tmp_path, "NSXUSD", consumer)

    assert tailer.poll_once(start + timedelta(minutes=60)) == 60
    _write_fixture(path, start, 90)
    assert tailer.poll_once(start + timedelta(minutes=90)) == 30
    assert [item.at for item in consumer.minutes] == [
        start + timedelta(minutes=offset) for offset in range(90)
    ]


def test_tailer_stale_alert_is_single_and_recovery_allows_feed(tmp_path: Path) -> None:
    start = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    consumer = Consumer()
    tailer = TaiLakeTailer(
        tmp_path,
        "NSXUSD",
        consumer,
        stale_after_seconds=300,
        clock=lambda: start,
    )

    assert tailer.poll_once(start + timedelta(minutes=4, seconds=59)) == 0
    assert consumer.stale_count == 0
    assert tailer.poll_once(start + timedelta(minutes=5)) == 0
    assert consumer.stale_count == 1
    assert tailer.poll_once(start + timedelta(minutes=6)) == 0
    assert consumer.stale_count == 1

    _write_fixture(tmp_path / "NSXUSD" / "1m_capital" / "2026" / "10.parquet", start, 1)
    assert tailer.poll_once(start + timedelta(minutes=6, seconds=20)) == 1
    assert consumer.recovered_count == 1
    assert tailer.stale is False


def test_tai_market_open_fails_closed_and_throttles_retries() -> None:
    from trader.api.app import _tai_market_open

    class Client:
        calls = 0

        def market_details(self, epic: str) -> dict[str, object]:
            self.calls += 1
            raise RuntimeError("unavailable")

    client = Client()
    is_open = _tai_market_open(client, "NSXUSD")
    now = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)

    assert is_open(now) is False
    assert is_open(now + timedelta(minutes=1)) is False
    assert client.calls == 1
    assert is_open(now + timedelta(minutes=11)) is False
    assert client.calls == 2


def test_tailer_logs_first_poll_and_fed_minutes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    start = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    _write_fixture(tmp_path / "NSXUSD" / "1m_capital" / "2026" / "10.parquet", start, 6)
    tailer = TaiLakeTailer(
        tmp_path, "NSXUSD", Consumer(), clock=lambda: start + timedelta(minutes=10)
    )

    with caplog.at_level("INFO", logger="trader.experiments.tai_feed"):
        assert tailer.poll_once(start + timedelta(minutes=10)) == 6
        assert tailer.poll_once(start + timedelta(minutes=10, seconds=20)) == 0

    lines = [r.getMessage() for r in caplog.records if "tai feed poll=" in r.getMessage()]
    assert len(lines) == 1
    assert "fed=6" in lines[0]
    assert "last_lake_minute=" in lines[0]
