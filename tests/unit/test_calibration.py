"""Fixtures for T-017 basis, spread and timezone calibration."""

from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from trader.data.calibration import calibrate, main, write_cost_model, write_report


def _rows(start: datetime, count: int = 10):
    hist = []
    capital = []
    for minute in range(count):
        stamp = start + timedelta(minutes=minute)
        value = 100 + (minute * minute) / 10
        hist.append({"ts_utc": stamp, "close": value, "bid": value, "ask": value + 0.5})
        capital.append({"ts_utc": stamp, "side": "bid", "close": value - 1, "spread_close": 0.2})
        capital.append({"ts_utc": stamp, "side": "ask", "close": value + 1, "spread_close": 0.2})
    ticks = [
        {"ts_utc": start, "bid": 100, "ask": 100.5},
        {"ts_utc": start + timedelta(minutes=1), "bid": 101, "ask": 101.5},
    ]
    return hist, capital, ticks


def test_calibration_consumes_generators_once_and_reports_session_metrics(tmp_path):
    start = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    hist, capital, ticks = _rows(start)
    result = calibrate(
        "NAS100",
        (row for row in hist),
        (row for row in capital),
        (row for row in ticks),
    )
    assert result.basis.count == 10
    assert result.basis.median == 1
    assert result.capital_spread.count == 10
    assert result.histdata_spread.p90 == 0.5
    session = result.session_stats["new_york"]
    assert session["basis"].count == 10
    assert session["histdata_spread"].count == 2
    assert session["capital_spread"].count == 10

    path = tmp_path / "basis.md"
    write_report([result], path)
    text = path.read_text(encoding="utf-8")
    assert "NAS100" in text
    assert "p99" in text
    assert "Session: new_york" in text


def test_timezone_check_detects_one_hour_shift():
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    capital = []
    hist = []
    for minute in range(500):
        base = 100 + (minute * minute) / 1000
        capital.append({"ts_utc": start + timedelta(minutes=minute), "close": base})
        hist.append({"ts_utc": start + timedelta(hours=1, minutes=minute), "close": base + 1})
    result = calibrate("XAUUSD", hist, capital, [], expected_lag_hours=0)
    assert result.best_lag_hours == 1
    assert result.timezone_ok is False


def test_missing_capital_data_is_explicit():
    result = calibrate(
        "XAUUSD",
        [{"ts_utc": datetime(2026, 1, 5, tzinfo=UTC), "close": 1}],
        [],
        [],
    )
    assert result.basis.count == 0
    assert result.capital_spread.count == 0
    assert "no overlapping" in result.warnings[0]


def test_cost_model_refuses_small_sessions_and_writes_lineage(tmp_path):
    hist, capital, ticks = _rows(datetime(2026, 1, 5, 13, tzinfo=UTC))
    result = calibrate("NAS100", hist, capital, ticks)
    output = tmp_path / "costs.yaml"
    with pytest.raises(ValueError, match="insufficient samples"):
        write_cost_model([result], output, {"inputs": []})
    write_cost_model(
        [result],
        output,
        {"inputs": [{"path": "fixture", "sha256": "abc", "row_count": 10}]},
        min_spread_samples=1,
        min_basis_samples=1,
    )
    text = output.read_text(encoding="utf-8")
    assert "lineage:" in text
    assert "capital_spread:" in text


def test_runner_reads_parquet_and_writes_both_outputs(tmp_path):
    hist_dir = tmp_path / "histdata"
    recorder_dir = tmp_path / "recorder"
    hist_dir.mkdir()
    recorder_dir.mkdir()
    hist, capital, _ticks = _rows(datetime(2026, 1, 5, 13, tzinfo=UTC), count=10)
    pq.write_table(pa.Table.from_pylist(hist), hist_dir / "hist.parquet")
    pq.write_table(pa.Table.from_pylist(capital), recorder_dir / "capital.parquet")
    report = tmp_path / "report.md"
    costs = tmp_path / "costs.yaml"
    assert (
        main(
            [
                "--histdata",
                str(hist_dir),
                "--recorder",
                str(recorder_dir),
                "--instrument",
                "NAS100",
                "--report",
                str(report),
                "--cost-model",
                str(costs),
                "--min-spread-samples",
                "1",
                "--min-basis-samples",
                "1",
            ]
        )
        == 0
    )
    assert report.exists()
    assert costs.exists()
