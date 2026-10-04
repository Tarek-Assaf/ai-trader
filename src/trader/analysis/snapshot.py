"""Deterministic, no-look-ahead market snapshots from normalized minute candles."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from trader.analysis.indicators import (
    adx,
    atr,
    bollinger,
    candle_patterns,
    confirmed_swings,
    daily_pivots,
    ema,
    macd,
    rsi,
    session_tags,
    session_vwap,
    sma,
)
from trader.data.resample import Candle, resample_ohlc


@dataclass(frozen=True, slots=True)
class TimeframeSnapshot:
    timeframe_minutes: int
    timestamp: datetime | None
    candle_count: int
    close: float | None
    ema_9: float | None
    ema_21: float | None
    sma_20: float | None
    rsi_14: float | None
    macd_line: float | None
    macd_signal: float | None
    macd_histogram: float | None
    atr_14: float | None
    bollinger_middle: float | None
    bollinger_upper: float | None
    bollinger_lower: float | None
    adx_14: float | None
    session_vwap: float | None
    swing_high: float | None
    swing_low: float | None
    pivot: float | None
    pivot_support_1: float | None
    pivot_resistance_1: float | None
    support_levels: tuple[float, ...]
    resistance_levels: tuple[float, ...]
    candle_patterns: tuple[str, ...]
    trend_regime: str
    volatility_regime: str
    session_tags: tuple[str, ...]
    spread_to_atr: float | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    instrument: str
    as_of: datetime
    freshness_seconds: float | None
    timeframe_agreement_score: float | None
    timeframes: tuple[TimeframeSnapshot, ...]

    def to_json(self) -> str:
        """Serialize with stable field order and UTC timestamps."""
        payload = asdict(self)
        payload["as_of"] = self.as_of.astimezone(UTC).isoformat()
        for timeframe in payload["timeframes"]:
            if timeframe["timestamp"] is not None:
                timeframe["timestamp"] = timeframe["timestamp"].astimezone(UTC).isoformat()
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


T = TypeVar("T")


def _last(values: tuple[T, ...] | list[T]) -> T | None:
    return values[-1] if values else None


def _latest_value(values: tuple[float | None, ...]) -> float | None:
    return next((value for value in reversed(values) if value is not None), None)


def _cluster_levels(
    values: tuple[float | None, ...],
    *,
    close: float | None,
    tolerance: float,
    resistance: bool,
) -> tuple[float, ...]:
    if close is None:
        return ()
    points = sorted(
        value
        for value in values
        if value is not None and (value >= close if resistance else value <= close)
    )
    clusters: list[list[float]] = []
    for value in points:
        if clusters and value - clusters[-1][-1] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    ranked = sorted(
        clusters,
        key=lambda cluster: (-len(cluster), abs(math.fsum(cluster) / len(cluster) - close)),
    )[:5]
    return tuple(sorted(math.fsum(cluster) / len(cluster) for cluster in ranked))


def _timeframe_snapshot(
    bars: list[Candle],
    timeframe: int,
    as_of: datetime,
    spread_by_time: Mapping[datetime, float],
) -> TimeframeSnapshot:
    visible = [
        bar
        for bar in bars
        if bar.complete and bar.ts_utc + timedelta(minutes=timeframe) <= as_of
    ]
    closes = [bar.close for bar in visible]
    fast, slow = ema(closes, 9), ema(closes, 21)
    averages, momentum, trend = sma(closes, 20), rsi(closes), macd(closes)
    ranges, trend_strength = atr(visible), adx(visible)
    bands, vwap = bollinger(closes), session_vwap(visible)
    swing_highs, swing_lows = confirmed_swings(visible)
    pivots = daily_pivots(visible)
    patterns = candle_patterns(visible)
    last_bar = visible[-1] if visible else None
    last_atr = _last(ranges)
    latest_spread = None
    if last_bar is not None:
        candidates = [
            (stamp, spread)
            for stamp, spread in spread_by_time.items()
            if stamp <= as_of and math.isfinite(spread) and spread >= 0
        ]
        if candidates:
            spread_stamp, spread_value = max(candidates, key=lambda item: item[0])
            if spread_stamp == last_bar.ts_utc and last_atr is not None and last_atr > 0:
                latest_spread = spread_value / last_atr

    ema_fast, ema_slow = _last(fast), _last(slow)
    adx_value = _last(trend_strength)
    trend_regime = "unknown"
    if ema_fast is not None and ema_slow is not None and adx_value is not None:
        if adx_value >= 25:
            trend_regime = "trending_up" if ema_fast > ema_slow else "trending_down"
        else:
            trend_regime = "ranging"
    atr_values = [value for value in ranges if value is not None]
    volatility_regime = "unknown"
    if last_atr is not None and atr_values:
        sorted_atr = sorted(atr_values)
        median = sorted_atr[len(sorted_atr) // 2]
        volatility_regime = "high" if median > 0 and last_atr >= median * 1.5 else "normal"
    latest_bands = _last(bands)
    last_pattern = _last(patterns) or ()
    last_pivot = _last(pivots)
    level_tolerance = max((last_atr or 0.0) * 0.25, (last_bar.close * 0.0001) if last_bar else 0.0)
    tags = session_tags(last_bar.ts_utc) if last_bar is not None else ()
    return TimeframeSnapshot(
        timeframe_minutes=timeframe,
        timestamp=last_bar.ts_utc if last_bar else None,
        candle_count=len(visible),
        close=last_bar.close if last_bar else None,
        ema_9=ema_fast,
        ema_21=ema_slow,
        sma_20=_last(averages),
        rsi_14=_last(momentum),
        macd_line=_last(tuple(point.line for point in trend)),
        macd_signal=_last(tuple(point.signal for point in trend)),
        macd_histogram=_last(tuple(point.histogram for point in trend)),
        atr_14=last_atr,
        bollinger_middle=latest_bands.middle if latest_bands else None,
        bollinger_upper=latest_bands.upper if latest_bands else None,
        bollinger_lower=latest_bands.lower if latest_bands else None,
        adx_14=adx_value,
        session_vwap=_last(vwap),
        swing_high=_latest_value(swing_highs),
        swing_low=_latest_value(swing_lows),
        pivot=last_pivot.pivot if last_pivot else None,
        pivot_support_1=last_pivot.support_1 if last_pivot else None,
        pivot_resistance_1=last_pivot.resistance_1 if last_pivot else None,
        support_levels=_cluster_levels(
            swing_lows,
            close=last_bar.close if last_bar else None,
            tolerance=level_tolerance,
            resistance=False,
        ),
        resistance_levels=_cluster_levels(
            swing_highs,
            close=last_bar.close if last_bar else None,
            tolerance=level_tolerance,
            resistance=True,
        ),
        candle_patterns=last_pattern,
        trend_regime=trend_regime,
        volatility_regime=volatility_regime,
        session_tags=tags,
        spread_to_atr=latest_spread,
    )


def build_snapshot(
    instrument: str,
    as_of: datetime,
    minute_candles: Iterable[Candle],
    *,
    spreads: Mapping[datetime, float] | None = None,
) -> Snapshot:
    """Build closed 1M/3M/5M views from 1M input, excluding future information."""
    if not instrument or not instrument.strip():
        raise ValueError("instrument must be a non-empty canonical symbol")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    cutoff = as_of.astimezone(UTC)
    source_bars = list(minute_candles)
    if any(bar.ts_utc.tzinfo is None or bar.ts_utc.utcoffset() is None for bar in source_bars):
        raise ValueError("candle timestamps must be timezone-aware")
    input_bars = [bar for bar in source_bars if bar.ts_utc.astimezone(UTC) < cutoff]
    input_bars.sort(key=lambda bar: (bar.ts_utc.astimezone(UTC), bar.series_key))
    # Only one canonical series may feed a snapshot; mixing bid/ask or symbols is unsafe.
    series = {bar.series_key for bar in input_bars}
    if len(series) > 1:
        raise ValueError("snapshot candles must belong to one series")
    normalized: list[Candle] = [
        Candle(
            ts_utc=bar.ts_utc.astimezone(UTC),
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            series_key=bar.series_key,
            complete=bar.complete,
            n_minutes=bar.n_minutes,
        )
        for bar in input_bars
    ]
    original_metadata = {
        (bar.ts_utc, bar.series_key): (bar.complete, bar.n_minutes) for bar in normalized
    }
    validated = resample_ohlc(normalized, 1)
    normalized = [
        Candle(
            ts_utc=bar.ts_utc,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            series_key=bar.series_key,
            complete=original_metadata[(bar.ts_utc, bar.series_key)][0],
            n_minutes=original_metadata[(bar.ts_utc, bar.series_key)][1],
        )
        for bar in validated
    ]
    spread_map: dict[datetime, float] = {}
    for stamp, value in (spreads or {}).items():
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("spread timestamps must be timezone-aware")
        utc_stamp = stamp.astimezone(UTC)
        if utc_stamp < cutoff:
            if not math.isfinite(value) or value < 0:
                raise ValueError("spreads must be finite and non-negative")
            spread_map[utc_stamp] = value
    views = (
        _timeframe_snapshot(normalized, 1, cutoff, spread_map),
        _timeframe_snapshot(resample_ohlc(normalized, 3), 3, cutoff, spread_map),
        _timeframe_snapshot(resample_ohlc(normalized, 5), 5, cutoff, spread_map),
    )
    observed = views[0].timestamp
    freshness = (cutoff - observed).total_seconds() if observed else None
    directions = [
        "up" if view.ema_9 > view.ema_21 else "down" if view.ema_9 < view.ema_21 else "flat"
        for view in views
        if view.ema_9 is not None and view.ema_21 is not None
    ]
    dominant = (
        max(("up", "down", "flat"), key=lambda item: (directions.count(item), item))
        if directions
        else None
    )
    agreement = (
        sum(direction == dominant for direction in directions) / len(directions)
        if directions
        else None
    )
    return Snapshot(
        instrument=instrument.strip().upper(),
        as_of=cutoff,
        freshness_seconds=freshness,
        timeframe_agreement_score=agreement,
        timeframes=views,
    )
