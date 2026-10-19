from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from scripts import lab_t180_hist_tai as harness

from trader.data.resample import Candle


def _bars(count: int = 420) -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(
            start + timedelta(minutes=3 * index),
            100.0 + index * 0.01,
            100.5 + index * 0.01,
            99.5 + index * 0.01,
            100.1 + index * 0.01,
            0.0,
            "fixture",
            True,
            3,
        )
        for index in range(count)
    )


def test_holdout_guard_refuses_before_reading_data():
    start = datetime(2026, 10, 6, tzinfo=UTC)
    end = datetime(2026, 10, 17, tzinfo=UTC)
    with pytest.raises(ValueError, match="sealed holdout"):
        harness.guard_window(start, end, datetime(2026, 10, 16, tzinfo=UTC))


def test_label_is_present_and_replay_is_deterministic():
    bars = _bars()
    first, first_features = harness._replay(bars, 3)
    second, second_features = harness._replay(bars, 3)
    report = harness.report({"cells": [], "window": {"start": "a", "end": "b"}, "safety": {}})
    assert harness.LABEL in report
    assert [item.entry_time for item in first] == [item.entry_time for item in second]
    assert first_features.buy_cross == second_features.buy_cross
    assert first_features.sell_cross == second_features.sell_cross
