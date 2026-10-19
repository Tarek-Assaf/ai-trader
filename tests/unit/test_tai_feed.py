from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trader.experiments.tai_feed import TaiLakeTailer
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
