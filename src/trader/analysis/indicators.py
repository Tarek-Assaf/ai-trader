"""Pure, deterministic technical indicators for normalized OHLC candles."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

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
            parts.extend(
                (abs(candle.high - previous_close), abs(candle.low - previous_close))
            )
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
    """ADX from rolling directional-movement and true-range means."""
    _period(period)
    output: list[float | None] = [None] * len(candles)
    if len(candles) < period * 2:
        return tuple(output)
    ranges = true_range(candles)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for previous, current in zip(candles[:-1], candles[1:], strict=True):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    dx_values: list[float | None] = [None] * len(candles)
    for index in range(period - 1, len(candles)):
        start = index - period + 1
        tr = math.fsum(ranges[start : index + 1])
        if tr <= 0:
            dx_values[index] = 0.0
            continue
        plus = 100.0 * math.fsum(plus_dm[start : index + 1]) / tr
        minus = 100.0 * math.fsum(minus_dm[start : index + 1]) / tr
        denominator = plus + minus
        dx_values[index] = (
            0.0 if denominator <= 0 else 100.0 * abs(plus - minus) / denominator
        )
    first = period * 2 - 2
    for index in range(first, len(candles)):
        window = [value for value in dx_values[index - period + 1 : index + 1] if value is not None]
        if len(window) == period:
            output[index] = math.fsum(window) / period
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
