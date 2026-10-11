"""Pure, deterministic technical indicators for normalized OHLC candles."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from trader.data.resample import Candle


def _period(period: int) -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("period must be a positive integer")


def _finite(values: Sequence[float]) -> None:
    if not all(math.isfinite(value) for value in values):
        raise ValueError("indicator inputs must be finite")


def sma(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    """Simple moving average, available once a complete window exists."""
    _period(period)
    _finite(values)
    output: list[float | None] = [None] * len(values)
    for end in range(period, len(values) + 1):
        output[end - 1] = math.fsum(values[end - period : end]) / period
    return tuple(output)


def ema(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    """EMA seeded with the first complete SMA window."""
    _period(period)
    _finite(values)
    output: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(output)
    current = math.fsum(values[:period]) / period
    output[period - 1] = current
    alpha = 2.0 / (period + 1)
    for index in range(period, len(values)):
        current += alpha * (values[index] - current)
        output[index] = current
    return tuple(output)


def rsi(values: Sequence[float], period: int = 14) -> tuple[float | None, ...]:
    """Wilder RSI. Flat windows return 50; no-loss/no-gain windows return 100/0."""
    _period(period)
    _finite(values)
    output: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return tuple(output)
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = math.fsum(gains[:period]) / period
    avg_loss = math.fsum(losses[:period]) / period

    def score(gain: float, loss: float) -> float:
        if gain == 0 and loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        if gain == 0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    output[period] = score(avg_gain, avg_loss)
    for index in range(period + 1, len(values)):
        change_index = index - 1
        avg_gain = (avg_gain * (period - 1) + gains[change_index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[change_index]) / period
        output[index] = score(avg_gain, avg_loss)
    return tuple(output)


@dataclass(frozen=True, slots=True)
class MacdPoint:
    line: float | None
    signal: float | None
    histogram: float | None


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[MacdPoint, ...]:
    """MACD using SMA-seeded EMAs; signal starts after nine valid MACD points."""
    _period(fast)
    _period(slow)
    _period(signal)
    if fast >= slow:
        raise ValueError("fast period must be less than slow period")
    fast_values, slow_values = ema(values, fast), ema(values, slow)
    line: list[float | None] = [
        f - s if f is not None and s is not None else None
        for f, s in zip(fast_values, slow_values, strict=True)
    ]
    first = next((i for i, value in enumerate(line) if value is not None), len(line))
    compact = [value for value in line[first:] if value is not None]
    signals = ema(compact, signal)
    result: list[MacdPoint] = []
    for index, value in enumerate(line):
        signal_value = signals[index - first] if index >= first else None
        result.append(
            MacdPoint(
                line=value,
                signal=signal_value,
                histogram=value - signal_value
                if value is not None and signal_value is not None
                else None,
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class Bands:
    middle: float | None
    upper: float | None
    lower: float | None


def bollinger(
    values: Sequence[float], period: int = 20, deviations: float = 2.0
) -> tuple[Bands, ...]:
    """Population-standard-deviation Bollinger bands."""
    _period(period)
    _finite(values)
    if not math.isfinite(deviations) or deviations < 0:
        raise ValueError("deviations must be finite and non-negative")
    result: list[Bands] = [Bands(None, None, None) for _ in values]
    for end in range(period, len(values) + 1):
        window = values[end - period : end]
        mean = math.fsum(window) / period
        variance = math.fsum((value - mean) ** 2 for value in window) / period
        width = deviations * math.sqrt(variance)
        result[end - 1] = Bands(mean, mean + width, mean - width)
    return tuple(result)


def true_range(candles: Sequence[Candle]) -> tuple[float, ...]:
    """True range per candle; the first observation uses high-low."""
    output: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        parts = [candle.high - candle.low]
        if previous_close is not None:
            parts.extend((abs(candle.high - previous_close), abs(candle.low - previous_close)))
        output.append(max(parts))
        previous_close = candle.close
    _finite(output)
    return tuple(output)


def atr(candles: Sequence[Candle], period: int = 14) -> tuple[float | None, ...]:
    """Wilder ATR seeded from the first complete true-range window."""
    _period(period)
    ranges = true_range(candles)
    output: list[float | None] = [None] * len(ranges)
    if len(ranges) < period:
        return tuple(output)
    current = math.fsum(ranges[:period]) / period
    output[period - 1] = current
    for index in range(period, len(ranges)):
        current = (current * (period - 1) + ranges[index]) / period
        output[index] = current
    return tuple(output)


def adx(candles: Sequence[Candle], period: int = 14) -> tuple[float | None, ...]:
    """Wilder ADX with Wilder-smoothed true range and directional movement."""
    _period(period)
    output: list[float | None] = [None] * len(candles)
    first_adx = 2 * period - 2
    if len(candles) <= first_adx:
        return tuple(output)
    ranges = true_range(candles)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for previous, current in zip(candles[:-1], candles[1:], strict=True):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    smoothed_range = math.fsum(ranges[:period])
    smoothed_plus = math.fsum(plus_dm[:period])
    smoothed_minus = math.fsum(minus_dm[:period])
    dx_values: list[float | None] = [None] * len(candles)
    for index in range(period - 1, len(candles)):
        if index >= period:
            smoothed_range += ranges[index] - smoothed_range / period
            smoothed_plus += plus_dm[index] - smoothed_plus / period
            smoothed_minus += minus_dm[index] - smoothed_minus / period
        if smoothed_range <= 0:
            dx_values[index] = 0.0
            continue
        plus = 100.0 * smoothed_plus / smoothed_range
        minus = 100.0 * smoothed_minus / smoothed_range
        denominator = plus + minus
        dx_values[index] = 0.0 if denominator <= 0 else 100.0 * abs(plus - minus) / denominator

    initial = [value for value in dx_values[period - 1 : first_adx + 1] if value is not None]
    if len(initial) != period:
        return tuple(output)
    current_adx = math.fsum(initial) / period
    output[first_adx] = current_adx
    for index in range(first_adx + 1, len(candles)):
        value = dx_values[index]
        if value is not None:
            current_adx = (current_adx * (period - 1) + value) / period
            output[index] = current_adx
    return tuple(output)


def session_vwap(candles: Sequence[Candle]) -> tuple[float | None, ...]:
    """UTC-calendar-day VWAP, reset at 00:00 UTC; zero volume stays unavailable."""
    result: list[float | None] = [None] * len(candles)
    current_day = None
    cumulative_price_volume = 0.0
    cumulative_volume = 0.0
    for index, candle in enumerate(candles):
        day = candle.ts_utc.date()
        if day != current_day:
            current_day = day
            cumulative_price_volume = 0.0
            cumulative_volume = 0.0
        if candle.volume < 0 or not math.isfinite(candle.volume):
            raise ValueError("volume must be finite and non-negative")
        typical = (candle.high + candle.low + candle.close) / 3.0
        cumulative_price_volume += typical * candle.volume
        cumulative_volume += candle.volume
        if cumulative_volume > 0:
            result[index] = cumulative_price_volume / cumulative_volume
    return tuple(result)


def confirmed_swings(
    candles: Sequence[Candle], left: int = 2, right: int = 2
) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    """Return swing points only once all right-hand confirmation bars are known."""
    _period(left)
    _period(right)
    highs: list[float | None] = [None] * len(candles)
    lows: list[float | None] = [None] * len(candles)
    for index in range(left, len(candles) - right):
        before = candles[index - left : index]
        after = candles[index + 1 : index + right + 1]
        candle = candles[index]
        if all(candle.high > other.high for other in (*before, *after)):
            highs[index] = candle.high
        if all(candle.low < other.low for other in (*before, *after)):
            lows[index] = candle.low
    return tuple(highs), tuple(lows)


def candle_patterns(candles: Sequence[Candle]) -> tuple[tuple[str, ...], ...]:
    """Classify deterministic engulfing, pin-bar, inside-bar, and doji patterns."""
    patterns: list[tuple[str, ...]] = [() for _ in candles]
    for index, candle in enumerate(candles):
        span = candle.high - candle.low
        body = abs(candle.close - candle.open)
        names: list[str] = []
        if span > 0 and body / span <= 0.1:
            names.append("doji")
        if span > 0:
            upper_wick = candle.high - max(candle.open, candle.close)
            lower_wick = min(candle.open, candle.close) - candle.low
            if max(upper_wick, lower_wick) >= 2 * max(body, span * 0.05):
                names.append("pin_bar")
        if index > 0:
            previous = candles[index - 1]
            if candle.high < previous.high and candle.low > previous.low:
                names.append("inside_bar")
            previous_bear = previous.close < previous.open
            current_bull = candle.close > candle.open
            previous_bull = previous.close > previous.open
            current_bear = candle.close < candle.open
            if (
                previous_bear
                and current_bull
                and candle.open <= previous.close
                and candle.close >= previous.open
            ) or (
                previous_bull
                and current_bear
                and candle.open >= previous.close
                and candle.close <= previous.open
            ):
                names.append("engulfing")
        patterns[index] = tuple(names)
    return tuple(patterns)


@dataclass(frozen=True, slots=True)
class PivotLevels:
    pivot: float
    support_1: float
    resistance_1: float


def daily_pivots(candles: Sequence[Candle]) -> tuple[PivotLevels | None, ...]:
    """Classic pivot levels from the previous observed UTC calendar day only."""
    output: list[PivotLevels | None] = [None] * len(candles)
    current_day = None
    day_high = day_low = day_close = None
    prior_levels: PivotLevels | None = None
    for index, candle in enumerate(candles):
        day = candle.ts_utc.date()
        if day != current_day:
            if day_high is not None and day_low is not None and day_close is not None:
                point = (day_high + day_low + day_close) / 3.0
                prior_levels = PivotLevels(
                    pivot=point,
                    support_1=2.0 * point - day_high,
                    resistance_1=2.0 * point - day_low,
                )
            current_day = day
            day_high, day_low = candle.high, candle.low
            day_close = candle.close
        else:
            day_high = max(day_high, candle.high) if day_high is not None else candle.high
            day_low = min(day_low, candle.low) if day_low is not None else candle.low
            day_close = candle.close
        output[index] = prior_levels
    return tuple(output)


def session_tags(stamp: datetime) -> tuple[str, ...]:
    """Return broad UTC session tags; no holiday or broker schedule is inferred."""
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("session timestamp must be timezone-aware")
    hour = stamp.astimezone(UTC).hour
    sessions = []
    if 0 <= hour < 9:
        sessions.append("asia")
    if 7 <= hour < 16:
        sessions.append("london")
    if 12 <= hour < 21:
        sessions.append("new_york")
    return tuple(sessions) or ("closed",)


def prior_day_levels(
    candles: Sequence[Candle],
) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    """High and low of the previous observed UTC calendar day; no look-ahead."""
    highs: list[float | None] = [None] * len(candles)
    lows: list[float | None] = [None] * len(candles)
    current_day = None
    day_high = day_low = None
    prior_high: float | None = None
    prior_low: float | None = None
    for index, candle in enumerate(candles):
        day = candle.ts_utc.date()
        if day != current_day:
            if day_high is not None and day_low is not None:
                prior_high, prior_low = day_high, day_low
            current_day = day
            day_high, day_low = candle.high, candle.low
        else:
            day_high = max(day_high, candle.high) if day_high is not None else candle.high
            day_low = min(day_low, candle.low) if day_low is not None else candle.low
        highs[index] = prior_high
        lows[index] = prior_low
    return tuple(highs), tuple(lows)


_NEW_YORK = ZoneInfo("America/New_York")
_OPEN = time(9, 30)
_OPEN_RANGE_END = time(10, 0)


def opening_range(
    candles: Sequence[Candle],
) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    """High and low of the first 30 minutes of the New York cash session.

    A value is available only from 10:00 New York time on the same day, so a
    bar never sees a range that is still forming.  Bars that straddle the
    window (such as 1h bars) do not form a range, which leaves it unavailable.
    """
    highs: list[float | None] = [None] * len(candles)
    lows: list[float | None] = [None] * len(candles)
    current_day = None
    range_high: float | None = None
    range_low: float | None = None
    for index, candle in enumerate(candles):
        local = candle.ts_utc.astimezone(_NEW_YORK)
        if local.date() != current_day:
            current_day = local.date()
            range_high = range_low = None
        clock = local.time()
        if _OPEN <= clock < _OPEN_RANGE_END:
            range_high = candle.high if range_high is None else max(range_high, candle.high)
            range_low = candle.low if range_low is None else min(range_low, candle.low)
        elif clock >= _OPEN_RANGE_END:
            highs[index] = range_high
            lows[index] = range_low
    return tuple(highs), tuple(lows)


_ASIA_START = time(18, 45)
_ASIA_END = time(22, 3)


def asia_range(
    candles: Sequence[Candle],
) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    """High and low of the overnight (Asia) window, 18:45-22:03 New York time.

    The range forms from bars that open inside the window and becomes visible
    only once the window has ended.  It then stays in force until the next
    window starts at 18:45 New York time, so a bar never sees a range that is
    still forming.  New York clock time is used, so US daylight saving moves
    the UTC hours automatically.
    """
    ranges: dict[object, tuple[float, float]] = {}
    highs: list[float | None] = [None] * len(candles)
    lows: list[float | None] = [None] * len(candles)
    for index, candle in enumerate(candles):
        local = candle.ts_utc.astimezone(_NEW_YORK)
        clock = local.time()
        day = local.date()
        if _ASIA_START <= clock < _ASIA_END:
            known = ranges.get(day)
            ranges[day] = (
                (candle.high, candle.low)
                if known is None
                else (max(known[0], candle.high), min(known[1], candle.low))
            )
            continue
        if clock >= _ASIA_END:
            source = ranges.get(day)
        elif clock < _ASIA_START:
            source = ranges.get(day - timedelta(days=1))
        else:  # pragma: no cover - the window branch above handles this
            source = None
        if source is not None:
            highs[index], lows[index] = source
    return tuple(highs), tuple(lows)


def new_york_minute(candles: Sequence[Candle]) -> tuple[float, ...]:
    """Minute of the New York day (0..1439) at which each bar opens."""
    values: list[float] = []
    for candle in candles:
        local = candle.ts_utc.astimezone(_NEW_YORK)
        values.append(float(local.hour * 60 + local.minute))
    return tuple(values)


def candle_shape(candles: Sequence[Candle], name: str) -> tuple[float | None, ...]:
    """1.0 when a bar has the named shape, else 0.0 (None until ATR exists).

    Shapes (all require a range of at least half an ATR(14) so noise is ignored):
    hammer_shape: lower wick >= 2x body and upper wick <= 10% of the range.
    inverted_shape: upper wick >= 2x body and lower wick <= 10% of the range.
    bull_engulfing: bullish body covers the previous bearish body and is larger.
    bear_engulfing: bearish body covers the previous bullish body and is larger.
    doji: body <= 10% of the range.
    """
    average = atr(candles, 14)
    values: list[float | None] = []
    for index, candle in enumerate(candles):
        size = average[index]
        if size is None:
            values.append(None)
            continue
        span = candle.high - candle.low
        body = abs(candle.close - candle.open)
        upper = candle.high - max(candle.open, candle.close)
        lower = min(candle.open, candle.close) - candle.low
        big_enough = span >= 0.5 * size
        hit = False
        if big_enough and span > 0:
            if name == "hammer_shape":
                hit = lower >= 2 * body and upper <= 0.1 * span
            elif name == "inverted_shape":
                hit = upper >= 2 * body and lower <= 0.1 * span
            elif name == "doji":
                hit = body <= 0.1 * span
            elif name in {"bull_engulfing", "bear_engulfing"} and index > 0:
                before = candles[index - 1]
                before_body = abs(before.close - before.open)
                if name == "bull_engulfing":
                    hit = (
                        before.close < before.open
                        and candle.close > candle.open
                        and candle.open <= before.close
                        and candle.close >= before.open
                        and body > before_body
                    )
                else:
                    hit = (
                        before.close > before.open
                        and candle.close < candle.open
                        and candle.open >= before.close
                        and candle.close <= before.open
                        and body > before_body
                    )
        values.append(1.0 if hit else 0.0)
    return tuple(values)
