from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.lab_t170_conditions import equal_weight_vwap, shifted_bar

from trader.data.resample import Candle


def _bars() -> tuple[Candle, ...]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(
            start + timedelta(minutes=3 * i),
            10.0 + i,
            12.0 + i,
            8.0 + i,
            11.0 + i,
            100.0,
            ("NSXUSD", "mid"),
            True,
            1,
        )
        for i in range(2)
    )


def test_equal_weight_vwap_ignores_volume_and_resets_day() -> None:
    bars = _bars()
    values = equal_weight_vwap(bars)
    assert values == (10.333333333333334, 11.333333333333334)


def test_shifted_bar_handles_boundaries() -> None:
    bars = _bars()
    assert shifted_bar(bars, 0, -1) == (None, None)
    assert shifted_bar(bars, 0, 1)[0] == 1
    assert shifted_bar(bars, None, 0) == (None, None)
