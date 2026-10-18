"""Small deterministic contracts for the T-151 forming-candle diagnostic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.lab_t151_owner_trades import OwnerEntry, Signal, forming_heikin_ashi, match_entries

from trader.data.resample import Candle


def _bar(index: int, open_price: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=3 * index),
        open_price,
        high,
        low,
        close,
        0.0,
        ("NAS100", "mid"),
        True,
        3,
    )


def test_forming_heikin_ashi_matches_hand_calculation() -> None:
    bars = (_bar(0, 100.0, 110.0, 90.0, 104.0), _bar(1, 104.0, 112.0, 101.0, 108.0))
    open_price, close = forming_heikin_ashi(bars)
    assert open_price == 102.0
    assert close == 103.5


def test_forming_partial_can_cross_then_reverse() -> None:
    first = forming_heikin_ashi((_bar(0, 100.0, 101.0, 98.0, 99.0),))
    bullish = forming_heikin_ashi((_bar(0, 100.0, 104.0, 98.0, 103.0),))
    bearish = forming_heikin_ashi((_bar(0, 100.0, 101.0, 95.0, 96.0),))
    assert first[1] < first[0]
    assert bullish[1] > bullish[0]
    assert bearish[1] < bearish[0]


def test_tolerance_matching_requires_same_direction_and_one_signal_per_entry() -> None:
    base = datetime(2026, 10, 6, 16, 3, tzinfo=UTC)
    entries = (
        OwnerEntry(base, "short", 100.0, 1.0),
        OwnerEntry(base + timedelta(minutes=30), "long", 101.0, 1.0),
    )
    signals = (
        Signal(base + timedelta(minutes=3), "short", 1),
        Signal(base + timedelta(minutes=33), "long", 2),
        Signal(base + timedelta(minutes=36), "long", 3),
    )
    result = match_entries(entries, signals, 1)
    assert result["hits"] == 2
    assert result["misses"] == 0
    assert result["extra_signals"] == 1
