"""Tests for deterministic analysis indicators and snapshots."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trader.analysis.indicators import adx, atr, bollinger, daily_pivots, ema, rsi, sma
from trader.analysis.snapshot import build_snapshot
from trader.data.resample import Candle


def _bars(count: int, *, start: datetime | None = None) -> list[Candle]:
    origin = start or datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    result = []
    for index in range(count):
        close = 100.0 + index
        result.append(
            Candle(
                ts_utc=origin + timedelta(minutes=index),
                open=close - 0.25,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=10.0,
                series_key=("NAS100", "bid"),
            )
        )
    return result


def test_indicators_match_checked_in_reference_values() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "analysis_reference.csv"
    with fixture.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    closes = [float(row["close"]) for row in rows]
    candles = [
        Candle(
            ts_utc=datetime(2026, 1, 5, 12, index, tzinfo=UTC),
            open=value - 0.5,
            high=value + 1.0,
            low=value - 1.0,
            close=value,
            volume=2.0,
        )
        for index, value in enumerate(closes)
    ]

    expected = rows[-1]
    assert sma(closes, 3)[-1] == pytest.approx(float(expected["sma_3"]))
    assert ema(closes, 3)[-1] == pytest.approx(float(expected["ema_3"]))
    assert rsi(closes, 3)[-1] == pytest.approx(float(expected["rsi_3"]))
    assert atr(candles, 3)[-1] == pytest.approx(float(expected["atr_3"]))
    bands = bollinger(closes, 3)[-1]
    assert bands.middle == pytest.approx(float(expected["bb_middle_3"]))
    assert bands.upper == pytest.approx(float(expected["bb_upper_3"]))
    assert bands.lower == pytest.approx(float(expected["bb_lower_3"]))
    assert adx(_bars(10), 2)[-1] == pytest.approx(100.0)


def test_indicator_invalid_periods_and_non_finite_input_fail() -> None:
    with pytest.raises(ValueError):
        sma([1.0], True)
    with pytest.raises(ValueError):
        ema([1.0, float("nan")], 1)
    with pytest.raises(ValueError):
        bollinger([1.0], 1, float("inf"))


def test_snapshot_builds_utc_aligned_timeframes_and_serializes_stably() -> None:
    bars = _bars(120)
    spread = {bars[-2].ts_utc: 0.5}
    cutoff = bars[-1].ts_utc + timedelta(minutes=1)

    snapshot = build_snapshot("nas100", cutoff, bars, spreads=spread)
    again = build_snapshot("NAS100", cutoff, bars, spreads=spread)
    decoded = json.loads(snapshot.to_json())

    assert snapshot == again
    assert snapshot.to_json() == again.to_json()
    assert [item.timeframe_minutes for item in snapshot.timeframes] == [1, 3, 5]
    assert all(item.candle_count > 0 for item in snapshot.timeframes)
    assert snapshot.timeframe_agreement_score is not None
    assert decoded["instrument"] == "NAS100"
    assert decoded["as_of"] == cutoff.isoformat()


def test_future_bars_cannot_change_snapshot_at_cutoff() -> None:
    bars = _bars(90)
    cutoff = bars[59].ts_utc + timedelta(minutes=1)

    before = build_snapshot("NAS100", cutoff, bars[:60])
    future = _bars(20, start=bars[-1].ts_utc + timedelta(minutes=1))
    after = build_snapshot("NAS100", cutoff, bars + future)

    assert before.to_json() == after.to_json()


def test_incomplete_resampled_bins_are_excluded() -> None:
    bars = _bars(8)
    cutoff = bars[-1].ts_utc + timedelta(minutes=1)

    snapshot = build_snapshot("NAS100", cutoff, bars)
    three_minute = snapshot.timeframes[1]

    assert three_minute.candle_count == 2
    assert three_minute.timestamp == bars[5].ts_utc


def test_pivots_use_only_the_previous_utc_day() -> None:
    day_one = [
        Candle(
            ts_utc=datetime(2026, 1, 5, hour, tzinfo=UTC),
            open=10.0,
            high=12.0,
            low=8.0,
            close=11.0,
            volume=1.0,
        )
        for hour in (0, 12)
    ]
    day_two = [
        Candle(
            ts_utc=datetime(2026, 1, 6, hour, tzinfo=UTC),
            open=11.0,
            high=13.0,
            low=9.0,
            close=12.0,
            volume=1.0,
        )
        for hour in (0, 12)
    ]

    levels = daily_pivots(day_one + day_two)

    assert levels[0] is None
    assert levels[1] is None
    assert levels[2] is not None
    assert levels[2].pivot == pytest.approx(31.0 / 3.0)
    assert levels[2].support_1 == pytest.approx(26.0 / 3.0)
    assert levels[2].resistance_1 == pytest.approx(38.0 / 3.0)


def test_incomplete_minute_input_is_excluded_and_naive_time_fails() -> None:
    bars = _bars(3)
    bars[-1] = Candle(
        ts_utc=bars[-1].ts_utc,
        open=bars[-1].open,
        high=bars[-1].high,
        low=bars[-1].low,
        close=bars[-1].close,
        volume=bars[-1].volume,
        series_key=bars[-1].series_key,
        complete=False,
        n_minutes=1,
    )

    snapshot = build_snapshot("NAS100", bars[-1].ts_utc + timedelta(minutes=1), bars)

    assert snapshot.timeframes[0].candle_count == 2
    with pytest.raises(ValueError):
        build_snapshot("NAS100", bars[-1].ts_utc.replace(tzinfo=None), bars)
