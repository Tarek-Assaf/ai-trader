from datetime import UTC, datetime, timedelta

from scripts import lab_t163_filter_anchor_diagnosis as diag

from trader.data.resample import Candle


def bars(count=36):
    start = datetime(2026, 10, 6, tzinfo=UTC)
    return tuple(
        Candle(start + timedelta(minutes=3 * i), 100.0, 101.0, 99.0, 100.0, 0.0)
        for i in range(count)
    )


def test_session_anchors_are_distinct():
    stamp = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
    assert diag._session_start(stamp, "utc_midnight").hour == 0
    assert diag._session_start(stamp, "ny_18:00").hour == 22
    assert diag._session_start(stamp, "ny_09:30").hour == 13


def test_day_first_groups_are_sequential():
    groups = diag._groups(bars(), "day_first")
    assert len(groups) == 2
    assert len(groups[0]) == 18


def test_zero_volume_vwap_is_equal_typical_price():
    values = diag._vwap_variant(bars(2), "utc_midnight_equal")
    assert values == (100.0, 100.0)


def test_tick_count_is_explicitly_unavailable():
    assert all(value is None for value in diag._vwap_variant(bars(2), "tick_count_unavailable"))
