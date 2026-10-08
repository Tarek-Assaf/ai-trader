"""Offline Strategy Lab job and HistData lake checks."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.parquet as pq

from trader.lab.runner import (
    LabJobManager,
    LabRunResult,
    coverage_report,
    load_histdata_candles,
    tune_variants,
)
from trader.lab.seeds import seed_specs


def _write_bars(root, symbol: str = "NSXUSD") -> None:
    rows = []
    for minute in range(20):
        stamp = datetime(2024, 1, 1, tzinfo=UTC).replace(minute=minute)
        rows.append(
            {"ts_utc": stamp, "open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5, "volume": 1.0}
        )
    path = root / symbol / "1m" / "2024" / "01.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_histdata_coverage_and_resampling(tmp_path) -> None:
    _write_bars(tmp_path)
    report = coverage_report(tmp_path, "NSXUSD")
    assert report.months == 1
    assert report.first_bar is not None and report.last_bar is not None
    assert len(load_histdata_candles(tmp_path, "NSXUSD", 3)) == 6


def test_seed_library_and_tuning_are_bounded_and_deterministic() -> None:
    seed, spec = seed_specs("XAUUSD", 5)[0]
    assert seed.source == "textbook"
    first = [item.model_dump(mode="json") for item in tune_variants(spec, limit=8)]
    second = [item.model_dump(mode="json") for item in tune_variants(spec, limit=8)]
    assert first == second
    assert len(first) <= 8


class _SlowRunner:
    def run(self, *, instruments):
        del instruments
        time.sleep(0.15)
        now = datetime.now(UTC)
        return LabRunResult("test", now, now, (), (), ())


def test_lab_job_manager_rejects_concurrent_run() -> None:
    manager = LabJobManager(_SlowRunner())
    assert manager.handle("/lab run gold") == "Strategy Lab run started."
    assert manager.handle("/lab run gold") == "Strategy Lab: a run is already in progress."
    manager._thread.join(timeout=2)  # noqa: SLF001 - deterministic fixture synchronization
