"""Known-answer checks for the owner export timezone conversion."""

from datetime import UTC, datetime

from scripts.lab_t160_owner_time_ny import parse_owner_datetime


def test_summer_and_winter_new_york_conversion() -> None:
    assert parse_owner_datetime("2026-10-06 10:00") == datetime(2026, 10, 6, 14, tzinfo=UTC)
    assert parse_owner_datetime("2026-12-01 10:00") == datetime(2026, 12, 1, 15, tzinfo=UTC)


def test_dst_fall_back_uses_earlier_fold() -> None:
    assert parse_owner_datetime("2026-11-01 01:30") == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
