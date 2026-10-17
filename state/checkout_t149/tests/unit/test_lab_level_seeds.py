"""Previous-day and opening-range levels, and the level seeds built on them."""

import random
from datetime import UTC, datetime, timedelta

from trader.analysis.indicators import opening_range, prior_day_levels
from trader.data.resample import Candle
from trader.lab.backtest import CostModel, run_backtest
from trader.lab.seeds import level_seed_specs


def _candle(stamp: datetime, high: float, low: float, close: float, minutes: int = 5) -> Candle:
    return Candle(
        ts_utc=stamp,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        series_key=("NSXUSD", "histdata"),
        n_minutes=minutes,
    )


def test_prior_day_levels_use_only_completed_days() -> None:
    day1 = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
    day2 = datetime(2026, 3, 3, 12, 0, tzinfo=UTC)
    candles = [
        _candle(day1, 110.0, 100.0, 105.0),
        _candle(day1 + timedelta(hours=1), 120.0, 95.0, 105.0),
        _candle(day2, 200.0, 50.0, 150.0),
    ]
    highs, lows = prior_day_levels(candles)
    assert highs[0] is None and lows[0] is None
    assert highs[1] is None and lows[1] is None  # still the first day
    assert highs[2] == 120.0 and lows[2] == 95.0  # previous day, not today's 200/50


def test_opening_range_forms_after_ten_and_respects_dst() -> None:
    # 9:30 New York is 14:30 UTC in winter and 13:30 UTC in summer.
    for day, open_utc_hour in ((datetime(2026, 1, 12), 14), (datetime(2026, 7, 13), 13)):
        start = day.replace(hour=open_utc_hour, minute=30, tzinfo=UTC)
        candles = [
            _candle(start + timedelta(minutes=5 * i), 100.0 + i, 90.0 - i, 95.0) for i in range(12)
        ]
        highs, lows = opening_range(candles)
        assert all(value is None for value in highs[:6])  # 9:30-9:55 still forming
        assert highs[6] == 105.0 and lows[6] == 85.0  # 10:00 onward sees bars 0..5
        assert highs[11] == 105.0 and lows[11] == 85.0


def test_hour_bars_do_not_form_an_opening_range() -> None:
    start = datetime(2026, 1, 12, 14, 0, tzinfo=UTC)  # 9:00 New York
    candles = [_candle(start + timedelta(hours=i), 100.0, 90.0, 95.0, minutes=60) for i in range(4)]
    highs, lows = opening_range(candles)
    assert all(value is None for value in highs) and all(value is None for value in lows)


def test_level_seeds_are_unique_valid_atr_scaled_on_every_timeframe() -> None:
    for timeframe in (1, 3, 5, 15, 60):
        seeds = level_seed_specs("NSXUSD", timeframe)
        names = [seed.name for seed, _ in seeds]
        assert len(names) == len(set(names)) == 32
        for _, spec in seeds:
            exits = [rule for rule in spec.exit_rules if rule.kind in {"stop", "target"}]
            assert exits and all(rule.unit == "atr" for rule in exits)
            assert any(rule.kind == "time_stop" for rule in spec.exit_rules)


def test_level_seeds_run_and_trade_rarely() -> None:
    rng = random.Random(21)
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    price = 1000.0
    minutes: list[Candle] = []
    for index in range(60 * 24 * 30):
        opening = price
        closing = opening + 0.01 + rng.gauss(0.0, 0.4)
        minutes.append(
            Candle(
                ts_utc=start + timedelta(minutes=index),
                open=opening,
                high=max(opening, closing) + 0.1,
                low=min(opening, closing) - 0.1,
                close=closing,
                volume=1.0,
                series_key=("NSXUSD", "histdata"),
                n_minutes=1,
            )
        )
        price = closing
    zero = CostModel(spread_points={"NSXUSD": 0.0}, slippage_points={"NSXUSD": 0.0})
    total = 0
    for timeframe in (15, 60):
        for _, spec in level_seed_specs("NSXUSD", timeframe):
            result = run_backtest(spec, minutes, cost_model=zero)
            total += len(result.trades)
            assert len(result.trades) / 30 < 12.0
    assert total > 0
