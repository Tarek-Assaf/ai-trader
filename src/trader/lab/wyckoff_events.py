"""Diagnostic event study for Wyckoff springs / upthrusts (docs/specs/round_d_wyckoff.md).

Read-only statistics on 5-minute bars: not a strategy and not a gate input.  Every
spring (accumulation) or upthrust (distribution) is found with the fixed definitions of
the spec, and what followed is measured in ATR units from the close of the event bar:
a long after a spring, a short after an upthrust, one ATR stop and one ATR target, 36
bars (the first barrier wins; ``neither`` counts the move at the horizon, capped at one
ATR).  Three stages are studied: the spring alone, the spring plus a successful test,
the spring plus a sign of strength.

PLACEBO ranges answer "is this Wyckoff, or just any line?": the same range shape without
the trend before it, and the same ranges with their edges shifted by +-0.75 of the range
width.  A pattern is only interesting if it beats these.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass

from trader.analysis import indicators
from trader.data.resample import Candle, resample_ohlc

RANGE_BARS = (48, 96, 192)
BREAK_ATR = (0.5, 0.25)  # normal and strict spring depth, in ATR
TREND_ATR = 3.0
MIN_RANGE_ATR, MAX_RANGE_ATR = 1.5, 6.0
FLAT_SLOPE_ATR_PER_10 = 0.25
TEST_BARS, SOS_BARS = 12, 10
HORIZON_BARS = 36
COOLDOWN_BARS = 12
MIN_EVENTS = 30
KINDS = ("wyckoff", "placebo_no_trend", "placebo_edge_up", "placebo_edge_down")
STAGES = ("spring", "spring_test", "spring_sos")


@dataclass(frozen=True, slots=True)
class WyckoffEvent:
    year: int
    kind: str
    range_bars: int
    break_atr: float
    side: str  # "accumulation" (long after a spring) or "distribution" (short)
    stage: str
    result_atr: float  # +1 target, -1 stop, else the capped move at the horizon


def volume_report(candles: Sequence[Candle]) -> dict[str, float]:
    """Share of bars whose volume is informative (not 0 and not one unit per minute)."""
    total = len(candles)
    if not total:
        return {"bars": 0.0, "informative_volume_share": 0.0}
    informative = sum(1 for c in candles if c.volume not in (0.0, float(c.n_minutes)))
    return {"bars": float(total), "informative_volume_share": informative / total}


def _result(bars: Sequence[Candle], index: int, long_side: bool, atr: float) -> float | None:
    end = index + 1 + HORIZON_BARS
    if end > len(bars):
        return None
    entry = bars[index].close
    sign = 1.0 if long_side else -1.0
    for bar in bars[index + 1 : end]:
        favourable = (bar.high - entry) if long_side else (entry - bar.low)
        adverse = (entry - bar.low) if long_side else (bar.high - entry)
        hit_target, hit_stop = favourable >= atr, adverse >= atr
        if hit_target and hit_stop:
            return 0.0
        if hit_target:
            return 1.0
        if hit_stop:
            return -1.0
    move = sign * (bars[end - 1].close - entry) / atr
    return max(-1.0, min(1.0, move))


def _range_arrays(
    bars: Sequence[Candle], n: int
) -> tuple[list[float | None], list[float | None], list[float | None], list[bool]]:
    """Prior-window high, low, ATR-scaled pre-trend and 'flat' flag for every bar."""
    atr = indicators.atr(list(bars), 14)
    closes = [bar.close for bar in bars]
    highs: deque[int] = deque()
    lows: deque[int] = deque()
    top: list[float | None] = [None] * len(bars)
    bottom: list[float | None] = [None] * len(bars)
    trend: list[float | None] = [None] * len(bars)
    flat = [False] * len(bars)
    for index in range(len(bars)):
        start = index - n
        if index >= 1:
            previous = index - 1
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
        unit = atr[index]
        if unit is None or unit <= 0 or start < 1:
            continue
        top[index], bottom[index] = bars[highs[0]].high, bars[lows[0]].low
        width = (top[index] or 0.0) - (bottom[index] or 0.0)
        slope = abs(closes[index - 1] - closes[start]) / (n / 10.0) / unit
        flat[index] = MIN_RANGE_ATR <= width / unit <= MAX_RANGE_ATR and (
            slope <= FLAT_SLOPE_ATR_PER_10
        )
        origin = start - 1 - 2 * n
        if origin >= 0:
            trend[index] = (closes[start - 1] - closes[origin]) / unit
    return top, bottom, trend, flat


def wyckoff_events(candles: Sequence[Candle]) -> tuple[WyckoffEvent, ...]:
    """Find springs/upthrusts (and placebos) with their tests and signs, on 5-minute bars."""
    bars = [bar for bar in resample_ohlc(candles, 5) if bar.complete]
    if len(bars) < 1000:
        return ()
    atr = indicators.atr(bars, 14)
    events: list[WyckoffEvent] = []
    for n in RANGE_BARS:
        top, bottom, trend, flat = _range_arrays(bars, n)
        for depth in BREAK_ATR:
            last_event: dict[tuple[str, str], int] = defaultdict(lambda: -(10**9))
            for index in range(n + 2 * n + 2, len(bars) - HORIZON_BARS - 1):
                unit, high_edge, low_edge, pre = atr[index], top[index], bottom[index], trend[index]
                if unit is None or high_edge is None or low_edge is None or not flat[index]:
                    continue
                if pre is None:
                    continue
                width = high_edge - low_edge
                bar = bars[index]
                for side in ("accumulation", "distribution"):
                    long_side = side == "accumulation"
                    with_trend = (pre <= -TREND_ATR) if long_side else (pre >= TREND_ATR)
                    for kind in KINDS:
                        if kind == "wyckoff" and not with_trend:
                            continue
                        if kind == "placebo_no_trend" and abs(pre) >= 1.0:
                            continue
                        if kind.startswith("placebo_edge") and not with_trend:
                            continue
                        shift = {"placebo_edge_up": 0.75, "placebo_edge_down": -0.75}.get(kind, 0.0)
                        level = (low_edge if long_side else high_edge) + shift * width
                        if long_side:
                            hit = level - depth * unit <= bar.low < level and bar.close > level
                        else:
                            hit = level < bar.high <= level + depth * unit and bar.close < level
                        if not hit or index - last_event[(kind, side)] < COOLDOWN_BARS:
                            continue
                        last_event[(kind, side)] = index
                        _record(
                            events, bars, atr, index, kind, n, depth, side, level, width, long_side
                        )
    return tuple(events)


def _record(
    events: list[WyckoffEvent],
    bars: Sequence[Candle],
    atr: Sequence[float | None],
    index: int,
    kind: str,
    n: int,
    depth: float,
    side: str,
    level: float,
    width: float,
    long_side: bool,
) -> None:
    year = bars[index].ts_utc.year
    unit = atr[index] or 0.0
    spring_bar = bars[index]
    spring_effort = spring_bar.high - spring_bar.low
    extreme = spring_bar.low if long_side else spring_bar.high
    midpoint = level + (width / 2 if long_side else -width / 2)

    def add(stage: str, at: int) -> None:
        value = _result(bars, at, long_side, atr[at] or unit)
        if value is not None:
            events.append(WyckoffEvent(year, kind, n, depth, side, stage, value))

    add("spring", index)
    test_done = sos_done = False
    for ahead in range(1, TEST_BARS + 1):
        at = index + ahead
        if at >= len(bars) - HORIZON_BARS - 1:
            break
        bar = bars[at]
        beyond = bar.low <= extreme if long_side else bar.high >= extreme
        if beyond:
            break  # the spring failed: price went back through its extreme
        if not test_done and (bar.high - bar.low) < spring_effort:
            add("spring_test", at)
            test_done = True
        sos = (bar.close > midpoint) if long_side else (bar.close < midpoint)
        if not sos_done and ahead <= SOS_BARS and sos and (bar.high - bar.low) >= unit:
            add("spring_sos", at)
            sos_done = True
        if test_done and sos_done:
            break


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarise_events(events: Sequence[WyckoffEvent]) -> dict[str, object]:
    """Per range length, depth, side and stage: mean result and lift versus placebo."""
    groups: dict[tuple[int, float, str, str, str], list[WyckoffEvent]] = defaultdict(list)
    for event in events:
        groups[(event.range_bars, event.break_atr, event.side, event.stage, event.kind)].append(
            event
        )
    rows: list[dict[str, object]] = []
    keys = {key[:4] for key in groups}
    for n, depth, side, stage in sorted(keys):
        real = groups.get((n, depth, side, stage, "wyckoff"), [])
        placebos = [
            value
            for kind in KINDS[1:]
            for value in [e.result_atr for e in groups.get((n, depth, side, stage, kind), [])]
        ]
        real_mean = _mean([e.result_atr for e in real])
        placebo_mean = _mean(placebos)
        by_year: dict[int, list[float]] = defaultdict(list)
        for event in real:
            by_year[event.year].append(event.result_atr)
        year_signs = [
            1 if (_mean(vals) or 0.0) > (placebo_mean or 0.0) else -1
            for vals in by_year.values()
            if len(vals) >= 5
        ]
        agreeing = sum(1 for sign in year_signs if sign == (1 if (real_mean or 0.0) > 0 else -1))
        rows.append(
            {
                "range_bars": n,
                "break_atr": depth,
                "side": side,
                "stage": stage,
                "events": len(real),
                "placebo_events": len(placebos),
                "mean_result_atr": None if real_mean is None else round(real_mean, 4),
                "placebo_mean_result_atr": None if placebo_mean is None else round(placebo_mean, 4),
                "lift_atr": (
                    None
                    if real_mean is None or placebo_mean is None
                    else round(real_mean - placebo_mean, 4)
                ),
                "enough_events": len(real) >= MIN_EVENTS,
                "years_agreeing": f"{agreeing}/{len(year_signs)}",
            }
        )
    return {"events": len(events), "rows": rows}


__all__ = [
    "KINDS",
    "STAGES",
    "WyckoffEvent",
    "summarise_events",
    "volume_report",
    "wyckoff_events",
]
