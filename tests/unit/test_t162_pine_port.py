from datetime import UTC, datetime, timedelta

import pytest
from scripts import lab_t162_pine_port as port

from trader.data.resample import Candle


def bars(count, highs=None, lows=None):
    start = datetime(2026, 10, 6, tzinfo=UTC)
    return bars_at(start, count, highs, lows)


def bars_at(start, count, highs=None, lows=None):
    highs = highs or [100.5] * count
    lows = lows or [99.5] * count
    return tuple(
        Candle(start + timedelta(minutes=3 * i), 100.0, highs[i], lows[i], 100.0)
        for i in range(count)
    )


def features(count, buys=(), sells=()):
    return port.Features(
        ema=(100.0,) * count,
        atr=(1.0,) * count,
        adx=(30.0,) * count,
        plus_di=(25.0,) * count,
        minus_di=(10.0,) * count,
        vwap=(99.0,) * count,
        ha_open=(99.0,) * count,
        ha_close=(101.0,) * count,
        buy_cross=tuple(i in buys for i in range(count)),
        sell_cross=tuple(i in sells for i in range(count)),
    )


def patch_simple(monkeypatch, count, buys=(), sells=()):
    monkeypatch.setattr(port, "compute_features", lambda _bars, _mode: features(count, buys, sells))
    monkeypatch.setattr(port, "_smart_ok", lambda *_args: True)
    monkeypatch.setattr(port, "_risk", lambda *_args: (True, 1.0))


def test_crossover_and_crossunder():
    buy, sell = port._crosses((None, 1.0, 1.0, 1.0), (None, 0.5, 1.5, 0.5))
    assert buy == (False, False, True, False)
    assert sell == (False, False, False, True)


def test_rma_seeds_at_period_and_wilders():
    values = port._rma((1.0, 2.0, 3.0, 5.0), 3)
    assert values[:2] == (None, None)
    assert values[2] == pytest.approx(2.0)
    assert values[3] == pytest.approx(3.0)


def test_refresh_entry_does_not_require_ha_cross(monkeypatch):
    count = 14
    highs = [100.5] * count
    highs[1] = 103.5
    patch_simple(monkeypatch, count, buys=(0,))
    trades, _ = port.replay(bars(count, highs=highs), "forming")
    assert [trade.entry_index for trade in trades] == [0, 13]


def test_entry_bar_target_first_and_tp3(monkeypatch):
    patch_simple(monkeypatch, 1, buys=(0,))
    trades, _ = port.replay(bars(1, highs=[103.5], lows=[98.0]), "forming")
    assert trades[0].exit_reason == "tp3"
    assert trades[0].exit_index == 0


def test_tp1_then_break_even(monkeypatch):
    patch_simple(monkeypatch, 2, buys=(0,))
    trades, _ = port.replay(bars(2, highs=[101.5, 100.5], lows=[99.5, 99.0]), "forming")
    assert trades[0].exit_reason == "be"
    assert trades[0].exit_index == 1
    assert trades[0].highest_target == 1


def test_same_bar_target_wins_over_stop(monkeypatch):
    patch_simple(monkeypatch, 1, buys=(0,))
    trades, _ = port.replay(bars(1, highs=[101.5], lows=[98.0]), "forming")
    assert trades[0].highest_target == 1
    assert trades[0].exit_reason is None
    assert trades[0].exit_index is None


def test_entry_bar_exit_is_recorded(monkeypatch):
    patch_simple(monkeypatch, 1, buys=(0,))
    trades, _ = port.replay(bars(1, highs=[103.5], lows=[99.5]), "forming")
    assert trades[0].entry_index == trades[0].exit_index == 0


def test_session_anchor_keeps_partial_last_group():
    values = bars_at(datetime(2026, 10, 5, 22, tzinfo=UTC), 19)
    opens, closes = port._ha_series(values, "forming", "session_1800_ny")
    assert opens[18] is not None
    assert closes[18] is not None
    closed_opens, closed_closes = port._ha_series(values, "closed", "session_1800_ny")
    assert closed_opens[18] is not None
    assert closed_closes[18] is not None


def test_session_anchor_is_dst_aware_and_cuts_weekend_gap():
    dst_bar = bars_at(datetime(2026, 11, 2, 23, tzinfo=UTC), 1)
    assert port._group_indices(dst_bar, "session_1800_ny") == [[0]]
    weekend = bars_at(datetime(2026, 10, 9, 22, tzinfo=UTC), 1)
    weekend += bars_at(datetime(2026, 10, 12, 22, tzinfo=UTC), 1)
    assert port._group_indices(weekend, "session_1800_ny") == [[0], [1]]


def test_rth_anchor_starts_at_nine_thirty_new_york():
    rth = bars_at(datetime(2026, 10, 6, 13, 30, tzinfo=UTC), 1)
    assert port._group_indices(rth, "rth_0930_ny") == [[0]]


def test_session_vwap_does_not_reset_at_utc_midnight():
    values = bars_at(
        datetime(2026, 10, 7, 23, 57, tzinfo=UTC),
        2,
        highs=[101.0, 103.0],
        lows=[99.0, 101.0],
    )
    session = port._vwap(values, "session_1800_ny")
    utc = port._vwap(values, "utc_midnight")
    assert session[1] == pytest.approx(101.0)
    assert utc[1] == pytest.approx(102.0)
