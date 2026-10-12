"""Pure sandbox evaluator for declarative strategies over closed candle frames."""

from __future__ import annotations

import heapq
import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from trader.analysis import indicators
from trader.analysis.indicators import session_tags
from trader.data.resample import Candle, resample_ohlc
from trader.lab.spec import IndicatorCondition, ParameterReference, StrategySpec

type IndicatorSeries = tuple[float | None, ...]


class IndicatorCache:
    """Reusable indicator and regime series for one immutable candle frame."""

    def __init__(self, bars: Iterable[Candle]) -> None:
        self.bars = tuple(bars)
        self._values: dict[tuple[str, int | None, int], IndicatorSeries] = {}
        self._volatility: tuple[str | None, ...] | None = None
        self._trend: tuple[str | None, ...] | None = None

    @classmethod
    def from_candles(cls, candles: Iterable[Candle], timeframe_minutes: int) -> IndicatorCache:
        bars = tuple(bar for bar in resample_ohlc(candles, timeframe_minutes) if bar.complete)
        return cls(bars)

    def get(self, indicator: str, period: int | None, lookback: int = 0) -> IndicatorSeries:
        key = (indicator, period, lookback)
        if key not in self._values:
            self._values[key] = _indicator_series(indicator, period, list(self.bars), lookback)
        return self._values[key]

    def volatility(self) -> tuple[str | None, ...]:
        if self._volatility is None:
            self._volatility = _volatility_regimes(indicators.atr(list(self.bars), 14))
        return self._volatility

    def trend(self) -> tuple[str | None, ...]:
        if self._trend is None:
            self._trend = _trend_regimes(list(self.bars))
        return self._trend


@dataclass(frozen=True, slots=True)
class EntrySignal:
    instrument: str
    timeframe_minutes: int
    timestamp_utc: datetime
    close: float


def _shift(values: IndicatorSeries, lookback: int) -> IndicatorSeries:
    if lookback <= 0:
        return values
    return tuple(
        None if index < lookback else values[index - lookback] for index in range(len(values))
    )


def _rolling(values: list[float], period: int, reducer: str) -> IndicatorSeries:
    result: list[float | None] = []
    for index in range(len(values)):
        window = values[max(0, index - period + 1) : index + 1]
        if len(window) < period:
            result.append(None)
        elif reducer == "max":
            result.append(max(window))
        else:
            result.append(min(window))
    return tuple(result)


FLAT_SLOPE_ATR_PER_10_BARS = 0.25
MAX_FLAT_RANGE_ATR = 6.0


def _range_context(indicator: str, period: int, bars: list[Candle]) -> IndicatorSeries:
    """Wyckoff range context measured on the ``period`` bars BEFORE the current bar.

    * ``flat_range_width_atr``: (highest high - lowest low of the prior ``period`` bars) /
      ATR(14), but only when the range is flat (absolute close drift over the range at most
      0.25 ATR per 10 bars) and not wider than 6 ATR; otherwise None, so a condition on it
      fails.  This keeps a Wyckoff "trading range" test inside the five-condition limit.
    * ``pre_trend_atr``: net close move over the ``2 * period`` bars before that range,
      divided by ATR(14); negative after a decline (accumulation), positive after a rise.

    The current bar never enters the window, so a break of the range cannot hide itself.
    """
    atr_values = indicators.atr(bars, 14)
    closes = [bar.close for bar in bars]
    result: list[float | None] = [None] * len(bars)
    highs: deque[int] = deque()  # indexes with decreasing highs (sliding maximum)
    lows: deque[int] = deque()  # indexes with increasing lows (sliding minimum)
    for index in range(len(bars)):
        start = index - period  # first bar of the range
        if index >= 1:
            previous = index - 1  # the window ends at the previous bar
            while highs and bars[highs[-1]].high <= bars[previous].high:
                highs.pop()
            highs.append(previous)
            while lows and bars[lows[-1]].low >= bars[previous].low:
                lows.pop()
            lows.append(previous)
        while highs and highs[0] < start:
            highs.popleft()
        while lows and lows[0] < start:
            lows.popleft()
        atr_value = atr_values[index]
        if atr_value is None or atr_value <= 0 or start < 1:
            continue
        if indicator == "flat_range_width_atr":
            width = (bars[highs[0]].high - bars[lows[0]].low) / atr_value
            slope_per_10 = abs(closes[index - 1] - closes[start]) / (period / 10.0) / atr_value
            if width <= MAX_FLAT_RANGE_ATR and slope_per_10 <= FLAT_SLOPE_ATR_PER_10_BARS:
                result[index] = width
        else:
            origin = start - 1 - 2 * period
            if origin >= 0:
                result[index] = (closes[start - 1] - closes[origin]) / atr_value
    return tuple(result)


def _indicator_series(
    indicator: str, period: int | None, bars: list[Candle], lookback: int = 0
) -> IndicatorSeries:
    closes = [bar.close for bar in bars]
    values: IndicatorSeries
    if indicator == "open":
        values = tuple(bar.open for bar in bars)
    elif indicator == "high":
        values = tuple(bar.high for bar in bars)
    elif indicator == "low":
        values = tuple(bar.low for bar in bars)
    elif indicator == "close":
        values = tuple(closes)
    elif indicator == "sma":
        assert period is not None
        values = indicators.sma(closes, period)
    elif indicator == "ema":
        assert period is not None
        values = indicators.ema(closes, period)
    elif indicator == "rsi":
        assert period is not None
        values = indicators.rsi(closes, period)
    elif indicator == "atr":
        assert period is not None
        values = indicators.atr(bars, period)
    elif indicator == "adx":
        assert period is not None
        values = indicators.adx(bars, period)
    elif indicator == "session_vwap":
        values = indicators.session_vwap(bars)
    elif indicator in {"rolling_high", "rolling_low"}:
        assert period is not None
        values = _rolling(
            [bar.high if indicator == "rolling_high" else bar.low for bar in bars],
            period,
            "max" if indicator == "rolling_high" else "min",
        )
    elif indicator in {"flat_range_width_atr", "pre_trend_atr"}:
        assert period is not None
        values = _range_context(indicator, period, bars)
    elif indicator in {"prior_day_high", "prior_day_low"}:
        day_highs, day_lows = indicators.prior_day_levels(bars)
        values = day_highs if indicator == "prior_day_high" else day_lows
    elif indicator in {"opening_range_high", "opening_range_low"}:
        range_highs, range_lows = indicators.opening_range(bars)
        values = range_highs if indicator == "opening_range_high" else range_lows
    elif indicator in {"asia_range_high", "asia_range_low"}:
        asia_highs, asia_lows = indicators.asia_range(bars)
        values = asia_highs if indicator == "asia_range_high" else asia_lows
    elif indicator == "ny_minute":
        values = indicators.new_york_minute(bars)
    elif indicator in {
        "hammer_shape",
        "inverted_shape",
        "bull_engulfing",
        "bear_engulfing",
        "doji",
    }:
        values = indicators.candle_shape(bars, indicator)
    elif indicator == "utc_minute":
        # Minute of the UTC day at which the bar opens (0..1439): time-of-day windows.
        values = tuple(float(bar.ts_utc.hour * 60 + bar.ts_utc.minute) for bar in bars)
    elif indicator in {"bollinger_upper", "bollinger_lower"}:
        assert period is not None
        mean = indicators.sma(closes, period)
        band: list[float | None] = []
        for index, average in enumerate(mean):
            window = closes[max(0, index - period + 1) : index + 1]
            if average is None or len(window) < period:
                band.append(None)
                continue
            variance = sum((value - average) ** 2 for value in window) / period
            deviation = math.sqrt(max(0.0, variance)) * 2.0
            band.append(
                average + deviation if indicator == "bollinger_upper" else average - deviation
            )
        values = tuple(band)
    else:
        raise ValueError("unsupported indicator")
    return _shift(values, lookback)


def _indicator_values(condition: IndicatorCondition, bars: list[Candle]) -> IndicatorSeries:
    return _indicator_series(condition.indicator, condition.period, bars)


def _volatility_regimes(values: IndicatorSeries) -> tuple[str | None, ...]:
    """Match the snapshot's expanding upper-median ATR classification without look-ahead."""
    lower: list[float] = []
    upper: list[float] = []
    result: list[str | None] = []
    for value in values:
        if value is None:
            result.append(None)
            continue
        if not math.isfinite(value):
            raise ValueError("indicator output must be finite")
        if not lower or value <= -lower[0]:
            heapq.heappush(lower, -value)
        else:
            heapq.heappush(upper, value)
        if len(lower) > len(upper) + 1:
            heapq.heappush(upper, -heapq.heappop(lower))
        elif len(upper) > len(lower):
            heapq.heappush(lower, -heapq.heappop(upper))
        median = -lower[0] if len(lower) > len(upper) else upper[0]
        result.append(
            "high_volatility" if median > 0 and value >= median * 1.5 else "normal_volatility"
        )
    return tuple(result)


def _trend_regimes(bars: list[Candle]) -> tuple[str | None, ...]:
    closes = [bar.close for bar in bars]
    fast, slow = indicators.ema(closes, 9), indicators.ema(closes, 21)
    strength = indicators.adx(bars, 14)
    regimes: list[str | None] = []
    for fast_value, slow_value, adx_value in zip(fast, slow, strength, strict=True):
        if fast_value is None or slow_value is None or adx_value is None:
            regimes.append(None)
        elif adx_value >= 25:
            regimes.append("trending_up" if fast_value > slow_value else "trending_down")
        else:
            regimes.append("ranging")
    return tuple(regimes)


def _matches(value: float | None, operator: str, threshold: float) -> bool:
    if value is None or not math.isfinite(value):
        return False
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    if operator == "eq":
        return value == threshold
    if operator == "ne":
        return value != threshold
    return False


def evaluate_entries(
    spec: StrategySpec,
    minute_candles: Iterable[Candle],
    *,
    indicator_cache: IndicatorCache | None = None,
) -> tuple[EntrySignal, ...]:
    """Evaluate conditions on complete bars; performs no network, file, or broker I/O."""
    source = list(minute_candles)
    if not source:
        return ()
    if len({candle.series_key for candle in source}) > 1:
        raise ValueError("strategy input must contain one candle series")
    bars = [bar for bar in resample_ohlc(source, spec.timeframe_minutes) if bar.complete]
    if not bars:
        return ()
    if not all(math.isfinite(bar.close) for bar in bars):
        raise ValueError("candle values must be finite")

    cache = indicator_cache or IndicatorCache(bars)
    if indicator_cache is not None and indicator_cache.bars != tuple(bars):
        raise ValueError("indicator cache does not match the candle frame")
    for condition in spec.entry_conditions:
        cache.get(condition.indicator, condition.period)
        if condition.threshold_indicator is not None:
            cache.get(
                condition.threshold_indicator,
                condition.threshold_period,
                condition.threshold_lookback,
            )

    volatility = cache.volatility()
    trend = cache.trend()
    atr_cache: dict[int, IndicatorSeries] = {}
    parameter_values = {parameter.name: parameter.value for parameter in spec.tunable_parameters}
    allowed_sessions = set(spec.session_filters)
    allowed_regimes = set(spec.regime_filters)
    signals: list[EntrySignal] = []

    for index, bar in enumerate(bars):
        if allowed_sessions and not allowed_sessions.intersection(session_tags(bar.ts_utc)):
            continue
        current_regimes = {trend[index], volatility[index]} - {None}
        if allowed_regimes and not allowed_regimes.intersection(current_regimes):
            continue
        for condition in spec.entry_conditions:
            raw_threshold = condition.threshold
            threshold = (
                parameter_values[raw_threshold.parameter]
                if isinstance(raw_threshold, ParameterReference)
                else raw_threshold
            )
            values = cache.get(condition.indicator, condition.period)
            if condition.threshold_indicator is not None:
                reference = cache.get(
                    condition.threshold_indicator,
                    condition.threshold_period,
                    condition.threshold_lookback,
                )[index]
                offset = condition.threshold_offset
                atr_multiple = condition.threshold_atr_multiple
                offset_value = (
                    parameter_values[offset.parameter]
                    if isinstance(offset, ParameterReference)
                    else float(offset)
                )
                atr_values = atr_cache.setdefault(
                    condition.threshold_atr_period,
                    cache.get("atr", condition.threshold_atr_period),
                )
                atr_value = atr_values[index]
                atr_multiple_value = (
                    parameter_values[atr_multiple.parameter]
                    if isinstance(atr_multiple, ParameterReference)
                    else float(atr_multiple)
                )
                if reference is None or atr_value is None:
                    break
                range_multiple = condition.threshold_range_multiple
                range_multiple_value = (
                    parameter_values[range_multiple.parameter]
                    if isinstance(range_multiple, ParameterReference)
                    else float(range_multiple)
                )
                range_term = 0.0
                if range_multiple_value != 0.0:
                    top = cache.get("asia_range_high", None)[index]
                    bottom = cache.get("asia_range_low", None)[index]
                    if top is None or bottom is None:
                        break
                    range_term = range_multiple_value * (top - bottom)
                threshold = reference + offset_value + atr_multiple_value * atr_value + range_term
            if not _matches(values[index], condition.operator, threshold):
                break
        else:
            signals.append(
                EntrySignal(
                    instrument=spec.instrument,
                    timeframe_minutes=spec.timeframe_minutes,
                    timestamp_utc=bar.ts_utc.astimezone(UTC),
                    close=bar.close,
                )
            )
    return tuple(signals)
