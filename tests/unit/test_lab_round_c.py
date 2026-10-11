"""Round C: overnight-range indicators, candle shapes, seeds and zone statistics."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from trader.analysis import indicators
from trader.data.resample import Candle
from trader.lab.engine import _indicator_series, evaluate_entries
from trader.lab.ostdv_stats import ostdv_days, summarise
from trader.lab.seeds import round_c_seed_specs
from trader.lab.spec import ComplexityLimits

NY = ZoneInfo("America/New_York")


def _bar(stamp: datetime, o: float, h: float, low: float, c: float, minutes: int = 1) -> Candle:
    return Candle(
        ts_utc=stamp.astimezone(UTC),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1.0,
        series_key=("NSXUSD", "histdata"),
        n_minutes=minutes,
    )


def _evening(day: datetime, *, high: float = 110.0, low: float = 100.0) -> list[Candle]:
    """One 1-minute bar every minute from 18:45 to 22:05 New York time on ``day``."""
    bars: list[Candle] = []
    stamp = day.replace(hour=18, minute=45, tzinfo=NY)
    while stamp < day.replace(hour=22, minute=6, tzinfo=NY):
        inside = stamp.hour * 60 + stamp.minute < 22 * 60 + 3
        bars.append(
            _bar(
                stamp,
                105.0,
                high if inside and stamp.minute == 45 else 106.0,
                low if inside and stamp.minute == 50 else 104.0,
                105.0,
            )
        )
        stamp += timedelta(minutes=1)
    return bars


def test_asia_range_hidden_until_the_window_ends_then_kept_overnight() -> None:
    bars = _evening(datetime(2026, 1, 12))
    # Summer day too: the same New York clock times, different UTC hours.
    bars += _evening(datetime(2026, 7, 13))
    highs, lows = indicators.asia_range(bars)
    first_end = next(
        i
        for i, b in enumerate(bars)
        if b.ts_utc.astimezone(NY).replace(tzinfo=None) >= datetime(2026, 1, 12, 22, 3)
    )
    assert all(value is None for value in highs[:first_end])
    assert highs[first_end] == 110.0 and lows[first_end] == 100.0
    # The next morning (before 18:45) still sees the same range.
    morning = [_bar(datetime(2026, 1, 13, 9, 0, tzinfo=NY), 105, 106, 104, 105)]
    mh, ml = indicators.asia_range(bars[: first_end + 1] + morning)
    assert (mh[-1], ml[-1]) == (110.0, 100.0)
    # In the evening of the next day the old range is replaced, not extended.
    assert highs[-1] == 110.0  # July day has its own identical range


def test_new_york_minute_follows_daylight_saving() -> None:
    winter = _bar(datetime(2026, 1, 12, 8, 30, tzinfo=NY), 1, 1, 1, 1)
    summer = _bar(datetime(2026, 7, 13, 8, 30, tzinfo=NY), 1, 1, 1, 1)
    assert indicators.new_york_minute([winter, summer]) == (510.0, 510.0)
    assert winter.ts_utc.hour != summer.ts_utc.hour


def _shape_bars(rows: list[tuple[float, float, float, float]]) -> list[Candle]:
    start = datetime(2026, 1, 12, 9, 0, tzinfo=NY)
    base = [_bar(start + timedelta(minutes=i), 100, 101, 99, 100) for i in range(20)]
    return base + [_bar(start + timedelta(minutes=20 + i), *row) for i, row in enumerate(rows)]


def test_candle_shapes() -> None:
    bars = _shape_bars(
        [
            (100.0, 100.2, 97.0, 100.1),  # hammer: long lower wick
            (100.0, 103.0, 99.9, 100.1),  # inverted: long upper wick
            (100.0, 100.0, 99.0, 99.2),  # small bearish
            (99.0, 101.5, 98.9, 101.4),  # bullish engulfing the small bearish bar
            (100.0, 101.0, 99.0, 100.05),  # doji
        ]
    )
    assert indicators.candle_shape(bars, "hammer_shape")[20] == 1.0
    assert indicators.candle_shape(bars, "inverted_shape")[21] == 1.0
    assert indicators.candle_shape(bars, "bull_engulfing")[23] == 1.0
    assert indicators.candle_shape(bars, "doji")[24] == 1.0
    assert indicators.candle_shape(bars, "hammer_shape")[21] == 0.0
    assert indicators.candle_shape(bars, "hammer_shape")[5] is None


def test_indicator_series_exposes_new_indicators() -> None:
    bars = _evening(datetime(2026, 1, 12))
    assert _indicator_series("asia_range_high", None, bars)[-1] == 110.0
    assert _indicator_series("ny_minute", None, bars)[0] == 18 * 60 + 45.0


def test_seed_library_is_valid_unique_and_within_limits() -> None:
    limits = ComplexityLimits(max_conditions_per_strategy=5, max_tunable_parameters=4)
    for instrument in ("NSXUSD", "XAUUSD"):
        for timeframe in (1, 3, 5, 15, 60):
            seeds = round_c_seed_specs(instrument, timeframe)
            names = [seed.name for seed, _ in seeds]
            assert len(names) == len(set(names)) == 172
            for _, spec in seeds:
                assert len(spec.entry_conditions) <= limits.max_conditions_per_strategy
    families = {seed.source for seed, _ in round_c_seed_specs("XAUUSD", 5)}
    assert families == {
        "ostdv_sweep",
        "ostdv_ote",
        "ostdv_fade",
        "ostdv_break",
        "ict_sweep_fvg",
        "ict_structure_shift",
        "candle_at_level",
    }


def test_fade_at_level_fires_only_after_poke_and_rejection() -> None:
    day = datetime(2026, 1, 12)
    bars = _evening(day)  # Asia range 100..110, width 10
    next_morning = datetime(2026, 1, 13, 3, 0, tzinfo=NY)
    # Level k=0.5 below the low = 95.  Bar 1 pokes to 94 and closes at 96 (rejection).
    bars.append(_bar(next_morning, 98, 98.5, 94.0, 96.0))
    bars.append(_bar(next_morning + timedelta(minutes=1), 96, 97, 95.5, 96.5))  # no poke
    spec = next(
        s for seed, s in round_c_seed_specs("NSXUSD", 1) if seed.name == "ostdv_fade_0.5_long_1.5_3"
    )
    signals = evaluate_entries(spec, bars)
    stamps = [signal.timestamp_utc for signal in signals]
    assert stamps == [next_morning.astimezone(UTC)]


def test_zone_statistics_measure_extension_in_range_widths() -> None:
    day = datetime(2026, 1, 12)
    bars = _evening(day)
    bars.append(_bar(datetime(2026, 1, 13, 4, 0, tzinfo=NY), 105, 135.0, 70.0, 105))
    days = ostdv_days(bars)
    assert len(days) == 1
    assert days[0].width == 10.0
    assert days[0].high_extension == 2.5 and days[0].low_extension == 3.0
    summary = summarise(days)
    assert summary["days"] == 1
    high = summary["high"]["overall"]["share_in_zone"]  # type: ignore[index]
    assert high["micro_smr_2.0_2.5"] == 1.0


def test_ote_zone_is_support_from_above_and_resistance_from_below() -> None:
    bars = _evening(datetime(2026, 1, 12))  # Asia range 100..110, width 10
    morning = datetime(2026, 1, 13, 3, 0, tzinfo=NY)
    # Buy zone: low + 0.5..0.62 ranges = 105..106.2.  Dips to 105.5, closes above at 107.
    bars.append(_bar(morning, 108, 108.5, 105.5, 107.0))
    # Sell zone: high - 0.62..0.5 ranges = 103.8..105.  Rises to 104.5, closes below at 103.
    bars.append(_bar(morning + timedelta(minutes=1), 102, 104.5, 101.5, 103.0))
    specs = {seed.name: spec for seed, spec in round_c_seed_specs("NSXUSD", 1)}
    long_hits = evaluate_entries(specs["ostdv_ote_any_long_1.5_3"], bars)
    short_hits = evaluate_entries(specs["ostdv_ote_any_short_1.5_3"], bars)
    assert [s.timestamp_utc for s in long_hits] == [morning.astimezone(UTC)]
    assert [s.timestamp_utc for s in short_hits] == [
        (morning + timedelta(minutes=1)).astimezone(UTC)
    ]
