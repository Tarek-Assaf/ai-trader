"""Diagnostic event study: what repeatedly happens when price reaches an OSTDV level?

Read-only statistics, not a strategy and not a gate input.  On 5-minute bars it
finds, for every trading day, side (above the Asia high / below the Asia low)
and level (k Asia-range widths beyond the edge), the FIRST bar that reaches the
level.  It then records what was visible at the close of the bar after the touch
(candle shape, whether price closed back inside, session, trend, ...) and what
happened next, measured from the level with one ATR as the unit:

* ``retrace``: price came back one ATR before it extended one ATR,
* ``continue``: price extended one ATR before it came back one ATR,
* ``neither``: neither happened within three hours (36 bars) or both in one bar.

Everything used as a "confirmation" is known before the outcome window starts,
so the table shows what was present beforehand, not what followed.  The same
study is run at made-up placebo levels between the named ones, so a pattern
that also shows up on arbitrary lines is not credited to the OSTDV levels.
Nothing here proves why the market moved; it counts what repeated.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import combinations
from zoneinfo import ZoneInfo

from trader.analysis import indicators
from trader.data.resample import Candle, resample_ohlc

_NEW_YORK = ZoneInfo("America/New_York")
NAMED_LEVELS = (0.1111, 0.5, 1.0, 1.5, 2.0, 2.272, 3.0, 3.5)
PLACEBO_LEVELS = (0.3, 0.75, 1.25, 1.75, 2.636, 3.25)
HORIZON_BARS = 36
MIN_EVENTS = 30
MIN_YEAR_EVENTS = 5
SHAPES = ("hammer_shape", "inverted_shape", "bull_engulfing", "bear_engulfing", "doji")


@dataclass(frozen=True, slots=True)
class LevelEvent:
    day: str
    year: int
    side: str  # "up" (above the Asia high) or "down" (below the Asia low)
    level: float
    kind: str  # "named" or "placebo"
    outcome: str  # "retrace", "continue", "neither"
    features: tuple[tuple[str, str], ...]


def _session(minute: int) -> str:
    if minute >= 18 * 60 or minute < 2 * 60:
        return "asia"
    if minute < 5 * 60:
        return "london_kz"
    if minute < 7 * 60:
        return "pre_ny"
    if minute < 10 * 60:
        return "ny_kz"
    return "ny_day"


def _near_side(side: str, level: float, price: float) -> bool:
    """True when price is back on the range side of the level."""
    return price < level if side == "up" else price > level


def _outcome(bars: Sequence[Candle], start: int, side: str, level: float, unit: float) -> str:
    """First of +-1 ATR from the level, scanning bars ``start`` .. ``start + HORIZON_BARS``."""
    for bar in bars[start : start + HORIZON_BARS]:
        if side == "up":
            went_on, came_back = bar.high >= level + unit, bar.low <= level - unit
        else:
            went_on, came_back = bar.low <= level - unit, bar.high >= level + unit
        if went_on and came_back:
            return "neither"
        if went_on:
            return "continue"
        if came_back:
            return "retrace"
    return "neither"


def level_events(candles: Sequence[Candle]) -> tuple[LevelEvent, ...]:
    """Build the first-touch events from 1-minute candles (one series)."""
    bars = [bar for bar in resample_ohlc(candles, 5) if bar.complete]
    if len(bars) < 300:
        return ()
    asia_high, asia_low = indicators.asia_range(bars)
    atr = indicators.atr(bars, 14)
    fast = indicators.ema([bar.close for bar in bars], 50)
    slow = indicators.ema([bar.close for bar in bars], 200)
    prior_high, prior_low = indicators.prior_day_levels(bars)
    shapes = {name: indicators.candle_shape(bars, name) for name in SHAPES}
    locals_ = [bar.ts_utc.astimezone(_NEW_YORK) for bar in bars]
    seen: set[tuple[date, str, float]] = set()
    window_end: dict[date, int] = {}
    events: list[LevelEvent] = []
    levels = [(level, "named") for level in NAMED_LEVELS] + [
        (level, "placebo") for level in PLACEBO_LEVELS
    ]
    for index in range(len(bars) - HORIZON_BARS - 3):
        high, low = asia_high[index], asia_low[index]
        unit = atr[index]
        if high is None or low is None or unit is None or high <= low or unit <= 0:
            continue
        local = locals_[index]
        minute = local.hour * 60 + local.minute
        if 17 * 60 <= minute < 22 * 60 + 3:
            continue  # market closed, or the overnight range is still forming
        day = (local + timedelta(hours=6)).date()
        window_end.setdefault(day, index)
        width = high - low
        bar = bars[index]
        for side in ("up", "down"):
            edge = high if side == "up" else low
            for depth, kind in levels:
                level = edge + depth * width if side == "up" else edge - depth * width
                reached = bar.high >= level if side == "up" else bar.low <= level
                key = (day, side + kind, depth)
                if not reached or key in seen:
                    continue
                seen.add(key)
                confirm = bars[index + 1]
                shape = next(
                    (name.removesuffix("_shape") for name in SHAPES if shapes[name][index] == 1.0),
                    "none",
                )
                fast_value, slow_value = fast[index], slow[index]
                with_trend = "unknown"
                if fast_value is not None and slow_value is not None:
                    bullish = fast_value > slow_value
                    with_trend = "with_trend" if bullish == (side == "up") else "counter_trend"
                prior = prior_high[index] if side == "up" else prior_low[index]
                swept_prior = "no"
                if prior is not None:
                    beat = bar.high > prior if side == "up" else bar.low < prior
                    swept_prior = "yes" if beat else "no"
                since = index - window_end[day]
                features = (
                    (
                        "touch_bar_close",
                        "rejected" if _near_side(side, level, bar.close) else "accepted",
                    ),
                    (
                        "confirm_bar_close",
                        "rejects" if _near_side(side, level, confirm.close) else "accepts",
                    ),
                    ("touch_bar_shape", shape),
                    ("session", _session(minute)),
                    ("trend", with_trend),
                    ("prior_day_swept", swept_prior),
                    (
                        "touch_bar_size",
                        "big" if (bar.high - bar.low) >= 1.5 * unit else "normal",
                    ),
                    (
                        "time_since_open_range",
                        "under_1h" if since < 12 else "under_3h" if since < 36 else "later",
                    ),
                )
                events.append(
                    LevelEvent(
                        day=str(day),
                        year=day.year,
                        side=side,
                        level=depth,
                        kind=kind,
                        outcome=_outcome(bars, index + 2, side, level, unit),
                        features=features,
                    )
                )
    return tuple(events)


def _rate(counter: Counter[str]) -> float | None:
    resolved = counter["retrace"] + counter["continue"]
    return counter["retrace"] / resolved if resolved else None


def _lift_table(
    events: Sequence[LevelEvent],
    selector: Callable[[LevelEvent], tuple[str, ...]],
) -> list[dict[str, object]]:
    """Retrace rate and consistency by year for every feature value (or pair)."""
    base = Counter(event.outcome for event in events)
    base_rate = _rate(base)
    groups: dict[str, list[LevelEvent]] = defaultdict(list)
    for event in events:
        for label in selector(event):
            groups[label].append(event)
    rows: list[dict[str, object]] = []
    for label, group in groups.items():
        counts = Counter(event.outcome for event in group)
        rate = _rate(counts)
        if rate is None or base_rate is None or sum(counts.values()) < MIN_EVENTS:
            continue
        by_year: dict[int, Counter[str]] = defaultdict(Counter)
        for event in group:
            by_year[event.year][event.outcome] += 1
        year_base: dict[int, Counter[str]] = defaultdict(Counter)
        for event in events:
            year_base[event.year][event.outcome] += 1
        agreeing = total = 0
        for year, year_counts in by_year.items():
            year_rate, year_base_rate = _rate(year_counts), _rate(year_base[year])
            if (
                year_rate is None
                or year_base_rate is None
                or year_counts["retrace"] + year_counts["continue"] < MIN_YEAR_EVENTS
            ):
                continue
            total += 1
            agreeing += (year_rate > year_base_rate) == (rate > base_rate)
        rows.append(
            {
                "pattern": label,
                "events": sum(counts.values()),
                "retrace": counts["retrace"],
                "continue": counts["continue"],
                "neither": counts["neither"],
                "retrace_rate": round(rate, 4),
                "base_retrace_rate": round(base_rate, 4),
                "lift": round(rate - base_rate, 4),
                "years_agreeing": f"{agreeing}/{total}",
            }
        )
    rows.sort(key=lambda row: abs(float(row["lift"])), reverse=True)  # type: ignore[arg-type]
    return rows


def _single(event: LevelEvent) -> tuple[str, ...]:
    return tuple(f"{name}={value}" for name, value in event.features)


def _pairs(event: LevelEvent) -> tuple[str, ...]:
    labels = _single(event)
    return tuple(f"{a} & {b}" for a, b in combinations(labels, 2))


def summarise_events(events: Sequence[LevelEvent], *, top: int = 8) -> dict[str, object]:
    """Per level and side: outcome counts, top single patterns and top pairs."""
    result: dict[str, object] = {"events": len(events)}
    for kind in ("named", "placebo"):
        levels: dict[str, object] = {}
        for depth in sorted({event.level for event in events if event.kind == kind}):
            for side in ("up", "down", "both"):
                chosen = [
                    event
                    for event in events
                    if event.kind == kind
                    and event.level == depth
                    and (side == "both" or event.side == side)
                ]
                counts = Counter(event.outcome for event in chosen)
                rate = _rate(counts)
                levels[f"k={depth:g}/{side}"] = {
                    "touches": len(chosen),
                    "retrace": counts["retrace"],
                    "continue": counts["continue"],
                    "neither": counts["neither"],
                    "retrace_rate": None if rate is None else round(rate, 4),
                    "top_single_patterns": _lift_table(chosen, _single)[:top],
                    "top_pairs": _lift_table(chosen, _pairs)[:top],
                }
        result[kind] = levels
    return result


__all__ = [
    "NAMED_LEVELS",
    "PLACEBO_LEVELS",
    "LevelEvent",
    "level_events",
    "summarise_events",
]
