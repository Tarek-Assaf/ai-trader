"""Diagnostic: where do daily highs and lows form relative to the overnight range?

Read-only statistics, not a strategy and not a gate input.  For every trading
day (from the 18:00 New York open to the 17:00 close) it measures how many
Asia-range widths the day's high sat above the Asia high, and the day's low
below the Asia low.  The summary then shows how often each extreme fell inside
the named OSTDV zones and in equally wide neighbouring bands, so a claim such as
"the high or low forms at the micro SMR 70-80% of the time" can be checked
against a neutral comparison on the owner's markets, year by year.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

from trader.data.resample import Candle

_NEW_YORK = ZoneInfo("America/New_York")
_ASIA_START = time(18, 45)
_ASIA_END = time(22, 3)

# Zones in ranges beyond the Asia edge (k).  Lines are given a +-0.05 band.
ZONES: dict[str, tuple[float, float]] = {
    "sweep_0_0.1111": (0.0, 0.1111),
    "retracement_0.5": (0.45, 0.55),
    "sb_1.0": (0.95, 1.05),
    "lv_1.5": (1.45, 1.55),
    "micro_smr_2.0_2.5": (2.0, 2.5),
    "smr_3.0": (2.95, 3.05),
    "extremes_3.5_4.0": (3.5, 4.0),
}
# Equally wide comparison bands next to the micro SMR and the extremes.
BASELINES: dict[str, tuple[float, float]] = {
    "band_1.5_2.0": (1.5, 2.0),
    "band_2.5_3.0": (2.5, 3.0),
    "band_3.0_3.5": (3.0, 3.5),
    "band_4.0_4.5": (4.0, 4.5),
}


@dataclass(frozen=True, slots=True)
class OstdvDay:
    trading_day: str
    year: int
    width: float
    high_extension: float
    low_extension: float


def ostdv_days(
    candles: Sequence[Candle],
    *,
    min_width: float = 0.0,
    window_start: time = _ASIA_START,
    window_end: time = _ASIA_END,
) -> tuple[OstdvDay, ...]:
    """One row per trading day that has a usable range (width > ``min_width``).

    The range window defaults to the owner's 18:45-22:03 New York time; other windows
    (for example the ICT Asia range 20:00-24:00) can be passed for a sensitivity check.
    ``window_end`` of 00:00 means midnight at the end of the day.
    """
    end_minute = window_end.hour * 60 + window_end.minute or 24 * 60
    start_minute = window_start.hour * 60 + window_start.minute
    asia: dict[date, list[float]] = {}
    extremes: dict[date, list[float]] = {}
    for candle in candles:
        local = candle.ts_utc.astimezone(_NEW_YORK)
        # The 18:00 open starts the next calendar trading day.
        day = (local + timedelta(hours=6)).date()
        known = extremes.get(day)
        if known is None:
            extremes[day] = [candle.high, candle.low]
        else:
            known[0] = max(known[0], candle.high)
            known[1] = min(known[1], candle.low)
        minute = local.hour * 60 + local.minute
        if start_minute <= minute < end_minute:
            window = asia.get(day)
            if window is None:
                asia[day] = [candle.high, candle.low]
            else:
                window[0] = max(window[0], candle.high)
                window[1] = min(window[1], candle.low)
    rows: list[OstdvDay] = []
    for day in sorted(asia):
        high, low = asia[day]
        width = high - low
        if width <= min_width:
            continue
        day_high, day_low = extremes[day]
        rows.append(
            OstdvDay(
                trading_day=str(day),
                year=day.year,
                width=width,
                high_extension=(day_high - high) / width,
                low_extension=(low - day_low) / width,
            )
        )
    return tuple(rows)


def _share(values: list[float], bounds: tuple[float, float]) -> float | None:
    if not values:
        return None
    inside = sum(1 for value in values if bounds[0] <= value <= bounds[1])
    return inside / len(values)


def _block(values: list[float]) -> dict[str, object]:
    zones = {name: _share(values, bounds) for name, bounds in {**ZONES, **BASELINES}.items()}
    reached = [value for value in values if value >= 2.0]
    return {
        "days": len(values),
        "share_in_zone": zones,
        "days_reaching_micro_smr": len(reached),
        "micro_smr_given_reached": _share(reached, ZONES["micro_smr_2.0_2.5"]),
    }


def _high_extension(row: OstdvDay) -> float:
    return row.high_extension


def _low_extension(row: OstdvDay) -> float:
    return row.low_extension


def summarise(days: Sequence[OstdvDay]) -> dict[str, object]:
    """Overall and per-year zone shares for the high side and the low side."""
    result: dict[str, object] = {"days": len(days)}
    getters: dict[str, Callable[[OstdvDay], float]] = {
        "high": _high_extension,
        "low": _low_extension,
    }
    for side, getter in getters.items():
        overall = [getter(row) for row in days]
        per_year: dict[int, list[float]] = defaultdict(list)
        for row in days:
            per_year[row.year].append(getter(row))
        result[side] = {
            "overall": _block(overall),
            "by_year": {str(year): _block(values) for year, values in sorted(per_year.items())},
        }
    return result


__all__ = ["BASELINES", "ZONES", "OstdvDay", "ostdv_days", "summarise"]
