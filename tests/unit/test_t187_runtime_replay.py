from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scripts import lab_t187_runtime_replay as replay


def _entry(minutes: int, direction: str, stop: str = "99.0") -> replay.RuntimeEntry:
    return replay.RuntimeEntry(
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minutes),
        direction,
        Decimal("100.0"),
        Decimal(stop),
    )


def test_compare_entries_matches_same_bar_and_one_tick_stop() -> None:
    expected = (_entry(3, "BUY", "99.0"), _entry(6, "SELL", "101.0"))
    actual = (_entry(3, "BUY", "99.09"), _entry(6, "SELL", "101.0"))

    result = replay.compare_entries(actual, expected, Decimal("0.1"))

    assert result["exact"] is True
    assert result["matched"] == 2
    assert result["misses"] == []
    assert result["extras"] == []


def test_compare_entries_reports_missing_and_extra_with_reason() -> None:
    expected = (_entry(3, "BUY"),)
    actual = (_entry(6, "SELL"),)

    result = replay.compare_entries(actual, expected, Decimal("0.1"))

    assert result["exact"] is False
    assert result["matched"] == 0
    assert result["misses"][0]["reason"] == "missing_runtime_direction"
    assert result["extras"][0]["reason"].startswith("runtime_signal_not_port_entry")
