"""UTC-aligned OHLC resampling shared by historical and live candle paths."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, slots=True)
class Candle:
    """One minute candle normalized from any supported source."""

    ts_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    series_key: tuple[str, ...] = ()
    complete: bool = True
    n_minutes: int = 1


def resample_ohlc(candles: Iterable[Candle], timeframe_minutes: int) -> list[Candle]:
    """Aggregate minute candles into UTC-aligned bars without filling data gaps.

    Bars are grouped by ``series_key`` so separate symbols, sides, or price
    bases can never be mixed. Incomplete bins are returned with ``complete``
    false and their observed minute count in ``n_minutes``.
    """
    if isinstance(timeframe_minutes, bool) or not isinstance(timeframe_minutes, int):
        raise ValueError("timeframe_minutes must be a positive integer")
    if timeframe_minutes <= 0:
        raise ValueError("timeframe_minutes must be a positive integer")

    grouped: dict[tuple[tuple[str, ...], datetime], list[Candle]] = defaultdict(list)
    seen: set[tuple[tuple[str, ...], datetime]] = set()
    for candle in candles:
        stamp = candle.ts_utc
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("candle timestamps must be timezone-aware")
        stamp = stamp.astimezone(UTC)
        if stamp.second or stamp.microsecond:
            raise ValueError("input candles must start on a minute boundary")
        values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("candle prices and volume must be finite")
        if candle.volume < 0 or candle.low > min(candle.open, candle.close):
            raise ValueError("candle OHLC or volume is invalid")
        if candle.high < max(candle.open, candle.close) or candle.high < candle.low:
            raise ValueError("candle OHLC is invalid")
        epoch_minute = int(stamp.timestamp() // 60)
        start_minute = epoch_minute - epoch_minute % timeframe_minutes
        bucket = datetime.fromtimestamp(start_minute * 60, UTC)
        key = (candle.series_key, stamp)
        if key in seen:
            raise ValueError("duplicate candle timestamp within a series")
        seen.add(key)
        grouped[(candle.series_key, bucket)].append(
            Candle(
                ts_utc=stamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                series_key=candle.series_key,
            )
        )

    output: list[Candle] = []
    interval = timedelta(minutes=1)
    for (series_key, bucket), members in sorted(grouped.items()):
        members.sort(key=lambda item: item.ts_utc)
        expected = [bucket + interval * offset for offset in range(timeframe_minutes)]
        actual = [member.ts_utc for member in members]
        output.append(
            Candle(
                ts_utc=bucket,
                open=members[0].open,
                high=max(member.high for member in members),
                low=min(member.low for member in members),
                close=members[-1].close,
                volume=sum(member.volume for member in members),
                series_key=series_key,
                complete=actual == expected,
                n_minutes=len(members),
            )
        )
    return output
