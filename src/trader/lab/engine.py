"""Pure sandbox evaluator for declarative strategies over closed candle frames."""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeAlias

from trader.analysis import indicators
from trader.analysis.indicators import session_tags
from trader.data.resample import Candle, resample_ohlc
from trader.lab.spec import IndicatorCondition, ParameterReference, StrategySpec

IndicatorSeries: TypeAlias = tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class EntrySignal:
    instrument: str
    timeframe_minutes: int
    timestamp_utc: datetime
    close: float


def _indicator_values(condition: IndicatorCondition, bars: list[Candle]) -> IndicatorSeries:
    closes = [bar.close for bar in bars]
    period = condition.period
    if condition.indicator == "close":
        return tuple(closes)
    if condition.indicator == "sma":
        assert period is not None
        return indicators.sma(closes, period)
    if condition.indicator == "ema":
        assert period is not None
        return indicators.ema(closes, period)
    if condition.indicator == "rsi":
        assert period is not None
        return indicators.rsi(closes, period)
    if condition.indicator == "atr":
        assert period is not None
        return indicators.atr(bars, period)
    if condition.indicator == "adx":
        assert period is not None
        return indicators.adx(bars, period)
    if condition.indicator == "session_vwap":
        return indicators.session_vwap(bars)
    raise ValueError("unsupported indicator")


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
    spec: StrategySpec, minute_candles: Iterable[Candle]
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

    cache: dict[tuple[str, int | None], IndicatorSeries] = {}
    for condition in spec.entry_conditions:
        key = (condition.indicator, condition.period)
        if key not in cache:
            cache[key] = _indicator_values(condition, bars)

    volatility = _volatility_regimes(indicators.atr(bars, 14))
    trend = _trend_regimes(bars)
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
            values = cache[(condition.indicator, condition.period)]
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
