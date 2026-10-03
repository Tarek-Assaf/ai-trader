from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from trader.data.market_hours import MarketHours, resolve_market_open


def _details() -> dict[str, object]:
    return {
        "instrument": {
            "openingHours": {
                "mon": ["09:00 - 12:00", "13:00 - 17:00"],
                "tue": ["09:00 - 12:00", "13:00 - 17:00"],
                "wed": ["09:00 - 12:00", "13:00 - 17:00"],
                "thu": ["09:00 - 12:00", "13:00 - 17:00"],
                "fri": ["09:00 - 12:00", "13:00 - 17:00"],
                "sat": [],
                "sun": ["23:00 - 00:00"],
                "zone": "America/New_York",
            }
        },
        "snapshot": {"marketStatus": "TRADEABLE"},
    }


def test_broker_hours_cover_open_daily_break_and_weekend() -> None:
    schedule = MarketHours.from_market_details(_details())
    assert schedule.is_open(datetime(2026, 10, 5, 14, 0, tzinfo=UTC))
    assert schedule.is_break(datetime(2026, 10, 5, 16, 30, tzinfo=UTC))
    assert not schedule.is_open(datetime(2026, 10, 3, 16, 0, tzinfo=UTC))


def test_holiday_and_early_close_overrides() -> None:
    schedule = MarketHours.from_market_details(
        _details(),
        holidays=(date(2026, 10, 5),),
        early_closes={date(2026, 10, 9): time(13, 0)},
    )
    assert not schedule.is_open(datetime(2026, 10, 5, 15, 0, tzinfo=UTC))
    assert schedule.upcoming_holidays(datetime(2026, 10, 4, tzinfo=UTC), 2) == [date(2026, 10, 5)]
    assert schedule.is_open(datetime(2026, 10, 9, 16, 30, tzinfo=UTC))
    assert not schedule.is_open(datetime(2026, 10, 9, 17, 30, tzinfo=UTC))
    assert schedule.next_close(datetime(2026, 10, 9, 16, 30, tzinfo=UTC)) == datetime(
        2026, 10, 9, 17, 0, tzinfo=UTC
    )


def test_next_open_skips_weekend_and_uses_broker_timezone() -> None:
    schedule = MarketHours.from_market_details(_details())
    assert schedule.next_open(datetime(2026, 10, 3, 15, 0, tzinfo=UTC)) == datetime(
        2026, 10, 4, 3, 0, tzinfo=UTC
    )


def test_broker_status_precedes_schedule_and_stale_feed_is_fallback() -> None:
    schedule = MarketHours.from_market_details(_details())
    now = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
    assert resolve_market_open(
        "CLOSED", schedule, now
    ) == (False, "broker")
    assert resolve_market_open(
        "TRADEABLE", schedule, now
    ) == (True, "broker")
    assert resolve_market_open(
        "unknown", schedule, now
    ) == (True, "schedule")
    assert resolve_market_open(
        "unknown", None, now
    ) == (None, "unknown")


def test_invalid_broker_hours_fail_closed() -> None:
    payload = {"instrument": {"openingHours": {"mon": "09:00 - 17:00"}}}
    with pytest.raises(ValueError, match="openingHours.mon"):
        MarketHours.from_market_details(payload)


def test_capital_com_opening_hours_minute_boundaries() -> None:
    payload = {
        "instrument": {
            "openingHours": {
                "mon": ["00:00 - 21:59", "23:05 - 00:00"],
                "tue": ["00:00 - 21:59", "23:05 - 00:00"],
                "wed": ["00:00 - 21:59", "23:05 - 00:00"],
                "thu": ["00:00 - 21:59", "23:05 - 00:00"],
                "fri": ["00:00 - 21:59"],
                "sat": [],
                "sun": ["23:05 - 00:00"],
                "zone": "UTC",
            }
        }
    }
    schedule = MarketHours.from_market_details(payload)

    assert schedule.is_open(datetime(2026, 10, 5, 21, 59, 30, tzinfo=UTC))
    assert not schedule.is_open(datetime(2026, 10, 5, 22, 0, tzinfo=UTC))
    assert schedule.is_open(datetime(2026, 10, 5, 23, 5, tzinfo=UTC))
