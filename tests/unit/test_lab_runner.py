"""Offline Strategy Lab job and HistData lake checks."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq

from trader.data.resample import Candle
from trader.lab.engine import IndicatorCache, evaluate_entries
from trader.lab.ledger import ExperimentLedger, ExperimentRecord
from trader.lab.runner import (
    LabJobManager,
    LabRunner,
    LabRunResult,
    RunFileLock,
    cap_worker_count,
    coverage_report,
    load_histdata_candles,
    load_histdata_columns,
    prepare_holdout,
    read_memory_limit,
    strategy_lab_holdout_months,
    strategy_lab_max_training_years,
    tune_variants,
)
from trader.lab.seeds import seed_specs
from trader.lab.spec import IndicatorCondition, StopExit, StrategySpec, TargetExit
from trader.lab.validator import strategy_hash


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


def test_columnar_loader_returns_read_only_numeric_arrays(tmp_path) -> None:
    _write_bars(tmp_path)
    series = load_histdata_columns(tmp_path, "NSXUSD")
    assert len(series) == 20
    assert not isinstance(series.opens[0], Candle)
    assert tuple(series.to_candles()) == load_histdata_candles(tmp_path, "NSXUSD")
    if hasattr(series.opens, "flags"):
        assert not series.opens.flags.writeable


def test_worker_cap_respects_small_fake_memory_limit() -> None:
    assert cap_worker_count(8, cpu_count=8, memory_limit_bytes=1024**3) == 1
    assert cap_worker_count(2, cpu_count=8, memory_limit_bytes=16 * 1024**3) == 2


def test_run_file_lock_refuses_second_owner(tmp_path) -> None:
    path = tmp_path / "run.lock"
    first = RunFileLock(path)
    first.acquire()
    try:
        try:
            RunFileLock(path).acquire()
        except RuntimeError as exc:
            assert "already in progress" in str(exc)
        else:
            raise AssertionError("second owner must be refused")
    finally:
        first.release()


def test_read_memory_limit_parses_cgroup_values(tmp_path) -> None:
    path = tmp_path / "memory.max"
    path.write_text("12345\n", encoding="ascii")
    assert read_memory_limit(path) == 12345
    path.write_text("max\n", encoding="ascii")
    assert read_memory_limit(path) is None


def test_histdata_coverage_reports_missing_month(tmp_path) -> None:
    for month in (1, 3):
        path = tmp_path / "NSXUSD" / "1m" / "2024" / f"{month:02}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "ts_utc": datetime(2024, month, 1, tzinfo=UTC),
                        "open": 1.0,
                        "high": 2.0,
                        "low": 0.0,
                        "close": 1.0,
                        "volume": 1.0,
                    }
                ]
            ),
            path,
        )
    assert coverage_report(tmp_path, "NSXUSD").gaps == 1


def test_seed_library_and_tuning_are_bounded_and_deterministic() -> None:
    seed, spec = seed_specs("XAUUSD", 5)[0]
    assert seed.source == "textbook"
    first = [item.model_dump(mode="json") for item in tune_variants(spec, limit=8)]
    second = [item.model_dump(mode="json") for item in tune_variants(spec, limit=8)]
    assert first == second
    assert len(first) <= 8


def test_all_textbook_seeds_fire_on_a_trending_fixture() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles = []
    price = 100.0
    for index in range(300):
        price += 0.05 if index % 20 else -0.2
        candles.append(
            Candle(
                start + timedelta(minutes=index),
                price,
                price + 0.3,
                price - 0.3,
                price,
                1.0,
                ("XAUUSD", "histdata"),
            )
        )
    assert all(evaluate_entries(spec, candles) for _, spec in seed_specs("XAUUSD", 1))


def test_prepare_holdout_copies_latest_months_and_runner_can_exclude_them(tmp_path) -> None:
    lake = tmp_path / "lake"
    for month in (1, 2, 3):
        path = lake / "XAUUSD" / "1m" / "2024" / f"{month:02}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "ts_utc": datetime(2024, month, 1, tzinfo=UTC),
                "open": 1.0,
                "high": 2.0,
                "low": 0.0,
                "close": 1.0,
                "volume": 1.0,
            }
        ]
        pq.write_table(pa.Table.from_pylist(rows), path)
    holdout = tmp_path / "holdout"
    copied = prepare_holdout(lake, holdout, instruments=("XAUUSD",), months=2)
    assert len(copied) == 2
    assert len(load_histdata_candles(lake, "XAUUSD", exclude_months={(2024, 2), (2024, 3)})) == 1
    assert set(prepare_holdout(lake, holdout, instruments=("XAUUSD",), months=2)) == set(copied)
    try:
        prepare_holdout(lake, holdout, instruments=("XAUUSD",), months=1)
    except ValueError as exc:
        assert "different month set" in str(exc)
    else:
        raise AssertionError("a second request must not shrink the sealed holdout")


class _SlowRunner:
    def run(self, *, instruments, stop_event=None):
        del instruments, stop_event
        time.sleep(0.15)
        now = datetime.now(UTC)
        return LabRunResult("test", now, now, (), (), ())


def test_lab_job_manager_rejects_concurrent_run() -> None:
    manager = LabJobManager(_SlowRunner())
    assert manager.handle("/lab run gold") == "Strategy Lab run started."
    assert manager.handle("/lab run gold") == "Strategy Lab: a run is already in progress."
    manager._thread.join(timeout=2)  # noqa: SLF001 - deterministic fixture synchronization


def _runner_spec(instrument: str = "NSXUSD") -> StrategySpec:
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction="long",
        timeframe_minutes=1,
        entry_conditions=(IndicatorCondition(indicator="close", operator="gt", threshold=0.0),),
        exit_rules=(StopExit(kind="stop", distance=1.0), TargetExit(kind="target", distance=0.2)),
    )


def _write_holdout(root, instrument: str = "NSXUSD") -> None:
    rows = [
        {
            "ts_utc": datetime(2024, 2, 1, tzinfo=UTC),
            "open": 1.0,
            "high": 2.0,
            "low": 0.0,
            "close": 1.0,
            "volume": 1.0,
        }
    ]
    path = root / instrument / "1m" / "2024" / "02.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_runner_skips_and_ledgers_repeated_strategy_hash(tmp_path, monkeypatch) -> None:
    lake = tmp_path / "lake"
    holdout = tmp_path / "holdout"
    _write_bars(lake)
    _write_holdout(holdout)
    spec = _runner_spec()
    digest = strategy_hash(spec.model_dump(mode="json"))
    ledger = ExperimentLedger(
        [
            ExperimentRecord(
                digest,
                {"source": "previous"},
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2024, 1, 1, tzinfo=UTC),
                {"verdict": "fail"},
            )
        ]
    )
    monkeypatch.setattr(LabRunner, "_candidates", lambda *args, **kwargs: [("seed:test", spec)])

    result = LabRunner(lake_root=lake, holdout_root=holdout, ledger=ledger).run(
        instruments=("NSXUSD",), timeframes=(1,), workers=1
    )

    assert len(result.attempts) == 1
    assert result.attempts[0].verdict == "skipped"
    assert result.attempts[0].reason == "duplicate_strategy_hash"
    assert any(
        record.strategy_hash == digest and record.metrics.get("verdict") == "skipped"
        for record in ledger.records()
    )


def test_runner_stop_file_preserves_partial_progress(tmp_path, monkeypatch) -> None:
    lake = tmp_path / "lake"
    holdout = tmp_path / "holdout"
    for instrument in ("NSXUSD", "XAUUSD"):
        _write_bars(lake, instrument)
        _write_holdout(holdout, instrument)
    stop_file = tmp_path / "stop"
    progress = tmp_path / "progress.json"

    def candidates(_runner, instrument, _timeframe, _sources, _candles):
        return [("seed:test", _runner_spec(instrument))]

    monkeypatch.setattr(LabRunner, "_candidates", candidates)

    def stop_after_first(self, *args, **kwargs):
        del self, args, kwargs
        stop_file.write_text("stop\n", encoding="utf-8")
        return ()

    monkeypatch.setattr("trader.lab.runner.ValidationPipeline.run", stop_after_first)
    result = LabRunner(lake_root=lake, holdout_root=holdout).run(
        instruments=("NSXUSD", "XAUUSD"),
        timeframes=(1,),
        workers=1,
        stop_file=stop_file,
        progress_path=progress,
    )

    assert len(result.attempts) == 1
    progress_payload = json.loads(progress.read_text(encoding="utf-8"))
    assert progress_payload["ideas_done"] == 1
    assert progress_payload["ideas_total"] == 2


def test_runner_results_stay_within_telegram_limit() -> None:
    now = datetime.now(UTC)
    from trader.lab.runner import LabAttempt

    rows = tuple(
        LabAttempt(f"strategy-{index:03d}", "NSXUSD", 1, "seed", "fail", "validation_failed")
        for index in range(200)
    )
    result = LabRunResult("run", now, now, rows, (), ())
    assert len(LabRunner.format_results(result)) < 4000


def test_holdout_months_are_read_from_strategy_lab_config(tmp_path) -> None:
    config = tmp_path / "strategy_lab.yaml"
    config.write_text("data:\n  holdout_months: 7\n", encoding="utf-8")
    assert strategy_lab_holdout_months(config) == 7


def test_loader_columns_match_reference_row_loader(tmp_path) -> None:
    _write_bars(tmp_path)
    path = tmp_path / "NSXUSD" / "1m" / "2024" / "01.parquet"
    table = pq.read_table(path)
    expected = tuple(
        Candle(
            ts_utc=row["ts_utc"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            series_key=("NSXUSD", "histdata"),
        )
        for row in table.to_pylist()
    )
    assert load_histdata_candles(tmp_path, "NSXUSD") == expected


def test_coverage_uses_parquet_metadata_and_matches_loaded_series(tmp_path) -> None:
    _write_bars(tmp_path)
    report = coverage_report(tmp_path, "NSXUSD")
    candles = load_histdata_candles(tmp_path, "NSXUSD")
    assert report.first_bar == candles[0].ts_utc
    assert report.last_bar == candles[-1].ts_utc


def test_series_cache_invalidates_for_lake_and_holdout_changes(tmp_path) -> None:
    lake = tmp_path / "lake"
    cache = tmp_path / "cache"
    _write_bars(lake)
    first = load_histdata_candles(lake, "NSXUSD", cache_root=cache)
    assert list(cache.glob("*.parquet"))
    path = lake / "NSXUSD" / "1m" / "2024" / "01.parquet"
    rows = pq.read_table(path).to_pylist()
    rows[0]["close"] = 99.0
    pq.write_table(pa.Table.from_pylist(rows), path)
    second = load_histdata_candles(lake, "NSXUSD", cache_root=cache)
    excluded = load_histdata_candles(lake, "NSXUSD", exclude_months={(2024, 1)}, cache_root=cache)
    assert first[0].close != second[0].close
    assert excluded == ()
    assert len(list(cache.glob("*.parquet"))) >= 3


def test_progress_records_phases_and_counts(tmp_path, monkeypatch) -> None:
    lake = tmp_path / "lake"
    holdout = tmp_path / "holdout"
    cache = tmp_path / "cache"
    progress = tmp_path / "progress.json"
    _write_bars(lake)
    _write_holdout(holdout)
    monkeypatch.setattr(LabRunner, "_candidates", lambda *args, **kwargs: [])
    LabRunner(lake_root=lake, holdout_root=holdout, cache_root=cache).run(
        instruments=("NSXUSD",), timeframes=(1,), progress_path=progress
    )
    payload = json.loads(progress.read_text(encoding="utf-8"))
    assert payload["phase"] == "done"
    assert {"loading", "done"}.issubset(payload["phase_timings_seconds"])
    assert payload["ideas_done"] == payload["ideas_total"] == 0
    assert "peak_rss_bytes" in payload
    assert payload["workers"] >= 1


def test_max_training_years_is_configured(tmp_path) -> None:
    config = tmp_path / "strategy_lab.yaml"
    config.write_text("data:\n  max_training_years: all\n", encoding="utf-8")
    assert strategy_lab_max_training_years(config) is None


def test_indicator_cache_matches_direct_evaluation() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles = tuple(
        Candle(
            start + timedelta(minutes=index),
            100 + index * 0.1,
            101 + index * 0.1,
            99 + index * 0.1,
            100 + index * 0.1,
            1.0,
            ("XAUUSD", "histdata"),
        )
        for index in range(60)
    )
    spec = _runner_spec("XAUUSD")
    cache = IndicatorCache.from_candles(candles, spec.timeframe_minutes)
    assert evaluate_entries(spec, candles) == evaluate_entries(spec, candles, indicator_cache=cache)
