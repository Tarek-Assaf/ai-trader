from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.engine import IndicatorCache, evaluate_entries
from trader.lab.seeds import round_d_seed_specs

START = datetime(2024, 1, 1, tzinfo=UTC)


def _five_minute_bars(bars: list[tuple[float, float, float, float]]) -> list[Candle]:
    """Expand (open, high, low, close) five-minute bars into complete minute candles."""
    candles: list[Candle] = []
    for number, (o, h, low, c) in enumerate(bars):
        base = START + timedelta(minutes=5 * number)
        path = [(o, o, o, o), (o, h, o, h), (h, h, low, low), (low, c, low, c), (c, c, c, c)]
        for offset, (po, ph, pl, pc) in enumerate(path):
            candles.append(Candle(base + timedelta(minutes=offset), po, ph, pl, pc, 1.0))
    return candles


def _accumulation_with_spring(period: int = 48) -> list[tuple[float, float, float, float]]:
    bars: list[tuple[float, float, float, float]] = []
    price = 200.0
    for _ in range(2 * period + 2):  # decline into the range (about 1 point per bar)
        bars.append((price, price + 0.3, price - 1.3, price - 1.0))
        price -= 1.0
    low = price
    previous = low + 4.25
    for number in range(period):  # slow 12-bar swings between low+0.5 and low+8
        current = low + 4.25 + 3.75 * math.sin(2 * math.pi * (number + 1) / 12)
        bars.append(
            (
                previous,
                max(previous, current) + 0.3,
                min(previous, current) - 0.3,
                current,
            )
        )
        previous = current
    range_low = min(bar[2] for bar in bars[-period:])
    # spring: pokes 0.4 below the range low, closes back inside the range
    bars.append((previous, previous + 0.5, range_low - 0.4, range_low + 1.5))
    return bars


def test_range_indicators_see_the_decline_and_the_flat_range() -> None:
    period = 48
    candles = _five_minute_bars(_accumulation_with_spring(period))
    cache = IndicatorCache.from_candles(candles, 5)
    trend = cache.get("pre_trend_atr", period)[-1]
    width = cache.get("flat_range_width_atr", period)[-1]
    assert trend is not None and trend < -3.0
    assert width is not None and 1.5 <= width <= 6.0


def test_a_steady_trend_is_not_a_flat_range() -> None:
    bars = [(100.0 + n, 101.5 + n, 99.5 + n, 101.0 + n) for n in range(200)]
    cache = IndicatorCache.from_candles(_five_minute_bars(bars), 5)
    assert cache.get("flat_range_width_atr", 48)[-1] is None


def test_spring_long_triggers_on_the_constructed_spring_only() -> None:
    candles = _five_minute_bars(_accumulation_with_spring(48))
    spec = next(
        s
        for seed, s in round_d_seed_specs("NSXUSD", 5)
        if seed.name == "wyckoff_spring_48_any_long_1_2"
    )
    signals = evaluate_entries(spec, candles)
    assert len(signals) == 1
    last_bar_open = START + timedelta(minutes=5 * (len(candles) // 5 - 1))
    assert signals[0].timestamp_utc == last_bar_open


def test_round_d_ideas_are_declarative_bounded_and_evaluate_without_error() -> None:
    ideas = round_d_seed_specs("XAUUSD", 15)
    assert len(ideas) == 72
    assert len({seed.name for seed, _ in ideas}) == 72
    candles = _five_minute_bars(_accumulation_with_spring(48))
    for _, spec in ideas:
        assert len(spec.entry_conditions) <= 5
        assert spec.tunable_parameters
        evaluate_entries(spec, candles)


def test_event_study_finds_the_constructed_spring_and_reports_placebo_lift() -> None:
    from trader.lab.wyckoff_events import summarise_events, volume_report, wyckoff_events

    pad = [
        (
            200.0 + 4.0 * math.sin(2 * math.pi * n / 12),
            204.5 + 4.0 * math.sin(2 * math.pi * (n + 1) / 12),
            195.5 + 4.0 * math.sin(2 * math.pi * (n + 1) / 12),
            200.0 + 4.0 * math.sin(2 * math.pi * (n + 1) / 12),
        )
        for n in range(1100)
    ]
    scenario = _accumulation_with_spring(48)
    bars = (
        pad + scenario + [(scenario[-1][3], scenario[-1][3] + 2, scenario[-1][3] - 1, 210.0)] * 60
    )
    candles = _five_minute_bars(bars)
    events = wyckoff_events(candles)
    assert any(
        e.kind == "wyckoff" and e.side == "accumulation" and e.stage == "spring" for e in events
    )
    summary = summarise_events(events)
    assert summary["events"] == len(events) and summary["rows"]
    assert volume_report(candles)["informative_volume_share"] == 0.0
