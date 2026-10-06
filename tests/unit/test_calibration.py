"""Fixtures for T-017 basis, spread and timezone calibration."""

from datetime import UTC, datetime, timedelta

from trader.data.calibration import calibrate, write_report


def test_calibration_reports_overlap_spreads_sessions_and_lag(tmp_path):
    start = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    hist = []
    capital = []
    for minute in range(10):
        stamp = start + timedelta(minutes=minute)
        hist.append({"ts_utc": stamp, "close": 100 + minute})
        capital.append({"ts_utc": stamp, "side": "bid", "close": 99 + minute, "spread_close": 0.2})
    ticks = [
        {"ts_utc": start, "bid": 100, "ask": 100.5},
        {"ts_utc": start, "bid": 101, "ask": 101.5},
    ]

    result = calibrate("NAS100", hist, capital, ticks)
    assert result.basis.count == 10
    assert result.basis.median == 1
    assert result.histdata_spread.p90 == 0.5
    assert result.capital_spread.p90 == 0.2
    assert result.session_stats["new_york"]["basis"].count == 10
    assert result.timezone_ok

    path = tmp_path / "basis.md"
    write_report([result], path)
    assert "NAS100" in path.read_text(encoding="utf-8")


def test_calibration_is_explicit_when_capital_data_is_missing():
    result = calibrate("XAUUSD", [{"ts_utc": datetime(2026, 1, 5, tzinfo=UTC), "close": 1}], [], [])
    assert result.basis.count == 0
    assert result.capital_spread.count == 0
    assert "no overlapping" in result.warnings[0]
