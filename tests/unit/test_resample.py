"""Contract tests for UTC-aligned, gap-aware minute resampling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings, strategies as st

from trader.data.resample import Candle, resample_ohlc


def _candle(stamp: datetime, close: float, *, series: tuple[str, ...] = ()) -> Candle:
    return Candle(
        ts_utc=stamp,
        open=close - 0.5,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=2.0,
        series_key=series,
    )


def test_three_minute_ohlc_uses_chronological_open_and_close() -> None:
    start = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    bars = [_candle(start + timedelta(minutes=i), 100 + i) for i in (2, 0, 1)]

    result = resample_ohlc(bars, 3)

    assert result == [
        Candle(
            ts_utc=start,
            open=99.5,
            high=103.0,
            low=99.0,
            close=102.0,
            volume=6.0,
            complete=True,
            n_minutes=3,
        )
    ]


def test_missing_minutes_are_flagged_and_never_synthesized() -> None:
    start = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

    result = resample_ohlc([_candle(start, 100), _candle(start + timedelta(minutes=2), 102)], 3)

    assert len(result) == 1
    assert result[0].complete is False
    assert result[0].n_minutes == 2
    assert result[0].close == 102


def test_series_are_resampled_independently_and_utc_normalized() -> None:
    utc = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    local = datetime.fromisoformat("2026-10-03T15:00:00+03:00")

    result = resample_ohlc(
        [_candle(utc, 100, series=("US100", "bid")), _candle(local, 200, series=("US100", "ask"))],
        5,
    )

    assert len(result) == 2
    assert {bar.series_key for bar in result} == {("US100", "bid"), ("US100", "ask")}
    assert all(bar.ts_utc == utc for bar in result)
    assert all(not bar.complete and bar.n_minutes == 1 for bar in result)


def test_later_period_data_cannot_change_an_earlier_bar() -> None:
    start = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    earlier = [_candle(start + timedelta(minutes=i), 100 + i) for i in range(3)]

    first = resample_ohlc(earlier, 3)[0]
    extended = resample_ohlc(earlier + [_candle(start + timedelta(minutes=3), 1000)], 3)[0]

    assert first == extended


@pytest.mark.parametrize(
    "bars, minutes",
    [
        ([], 0),
        ([], True),
        ([_candle(datetime(2026, 10, 3, 12, 0), 100)], 3),
        ([_candle(datetime(2026, 10, 3, 12, 0, tzinfo=UTC).replace(second=1), 100)], 3),
        (
            [
                _candle(datetime(2026, 10, 3, 12, 0, tzinfo=UTC), 100),
                _candle(datetime(2026, 10, 3, 12, 0, tzinfo=UTC), 101),
            ],
            3,
        ),
    ],
)
def test_invalid_timeframes_and_duplicate_or_malformed_input_fail(bars, minutes) -> None:
    with pytest.raises(ValueError):
        resample_ohlc(bars, minutes)


def test_identical_normalized_minutes_produce_identical_historical_and_live_output() -> None:
    start = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    source_minutes = [_candle(start + timedelta(minutes=i), 100 + i) for i in range(5)]

    historical = resample_ohlc(source_minutes, 5)
    live = resample_ohlc(list(source_minutes), 5)

    assert historical == live


@settings(max_examples=100, deadline=None)
@given(
    samples=st.lists(
        st.tuples(
            st.floats(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0, max_value=5, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
        ),
        min_size=1,
        max_size=20,
    )
)
def test_ohlc_resampling_properties_and_no_lookahead(
    samples: list[tuple[float, float, float, float]],
) -> None:
    """Property-check OHLC invariants and isolation from the next timeframe bin."""
    timeframe = len(samples)
    anchor = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    epoch_minute = int(anchor.timestamp() // 60)
    bucket_minute = epoch_minute - epoch_minute % timeframe
    start = datetime.fromtimestamp(bucket_minute * 60, UTC)
    bars = [
        Candle(
            ts_utc=start + timedelta(minutes=minute),
            open=open_price,
            high=max(open_price, close) + wick,
            low=min(open_price, close) - wick,
            close=close,
            volume=volume,
        )
        for minute, (open_price, close, wick, volume) in enumerate(samples)
    ]

    result = resample_ohlc(list(reversed(bars)), timeframe)

    assert len(result) == 1
    aggregate = result[0]
    assert aggregate.complete is True
    assert aggregate.n_minutes == timeframe
    assert aggregate.open == bars[0].open
    assert aggregate.high == max(bar.high for bar in bars)
    assert aggregate.low == min(bar.low for bar in bars)
    assert aggregate.close == bars[-1].close
    assert aggregate.volume == pytest.approx(sum(bar.volume for bar in bars))

    future = Candle(
        ts_utc=start + timedelta(minutes=timeframe),
        open=5000,
        high=5002,
        low=4998,
        close=5001,
        volume=500,
    )
    extended = resample_ohlc([*bars, future], timeframe)
    assert extended[0] == aggregate
