from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

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


def test_window_counts_exclude_warmup_signals_and_trades(monkeypatch):
    bars = _bars(40)
    start = bars[10].ts_utc
    end = bars[20].ts_utc
    buy_cross = [False] * len(bars)
    buy_cross[2] = True
    buy_cross[12] = True
    features = SimpleNamespace(buy_cross=tuple(buy_cross), sell_cross=(False,) * len(bars))
    warmup_trade = harness.port.PortTrade(
        "long", 2, bars[2].ts_utc, 100.0, 99.0, 3, bars[3].ts_utc, "stop", 0
    )
    window_trade = harness.port.PortTrade(
        "long", 12, bars[12].ts_utc, 100.0, 99.0, 13, bars[13].ts_utc, "stop", 0
    )
    monkeypatch.setattr(harness, "_bars_for_window", lambda *args: bars)
    monkeypatch.setattr(harness, "_replay", lambda *args: ((warmup_trade, window_trade), features))
    costs = SimpleNamespace(for_bar=lambda *args: (1.0, 0.5))

    cell = harness.run_cell(Path("."), "NSXUSD", 3, start, end, costs)

    assert cell["warmup_bars"] == 10
    assert cell["window_bars"] == 10
    assert cell["signal_count"] == 1
    assert cell["discarded_warmup_trades"] == 1
    assert cell["trade_count"] == 1
    assert cell["zero_trade_reason"] is None


def test_zero_trade_reason_is_not_exposed_when_port_has_no_filter_counters(monkeypatch):
    bars = _bars(40)
    start = bars[10].ts_utc
    end = bars[20].ts_utc
    buy_cross = [False] * len(bars)
    buy_cross[12] = True
    features = SimpleNamespace(buy_cross=tuple(buy_cross), sell_cross=(False,) * len(bars))
    monkeypatch.setattr(harness, "_bars_for_window", lambda *args: bars)
    monkeypatch.setattr(harness, "_replay", lambda *args: ((), features))
    costs = SimpleNamespace(for_bar=lambda *args: (1.0, 0.5))

    cell = harness.run_cell(Path("."), "NSXUSD", 3, start, end, costs)

    assert cell["signal_count"] == 1
    assert cell["trade_count"] == 0
    assert cell["zero_trade_reason"] == "not exposed"
