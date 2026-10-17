"""OSTDV level event study: first touches, outcomes and summaries."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from trader.data.resample import Candle
from trader.lab.ostdv_events import level_events, summarise_events

NY = ZoneInfo("America/New_York")


def _bar(stamp: datetime, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(
        ts_utc=stamp.astimezone(UTC),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1.0,
        series_key=("NSXUSD", "histdata"),
    )


def _market(days: int, quiet: bool = True) -> list[Candle]:
    """Four-ish trading days of quiet 1-minute bars with a 100..110 overnight range."""
    bars: list[Candle] = []
    stamp = datetime(2026, 1, 12, 18, 0, tzinfo=NY)
    end = stamp + timedelta(days=days)
    while stamp < end:
        minute = stamp.hour * 60 + stamp.minute
        if 17 * 60 <= minute < 18 * 60:
            stamp += timedelta(minutes=1)
            continue
        high, low = 105.2, 104.8
        if 18 * 60 + 45 <= minute < 22 * 60 + 3:
            if minute == 18 * 60 + 45:
                high = 110.0
            if minute == 18 * 60 + 50:
                low = 100.0
        bars.append(_bar(stamp, 105.0, high, low, 105.0))
        stamp += timedelta(minutes=1)
    return bars


def _inject(bars: list[Candle], start: datetime, rows: list[tuple[float, float, float, float]]):
    """Replace consecutive 1-minute bars from ``start`` with the given OHLC rows."""
    by_time = {bar.ts_utc: index for index, bar in enumerate(bars)}
    for offset, row in enumerate(rows):
        stamp = (start + timedelta(minutes=offset)).astimezone(UTC)
        bars[by_time[stamp]] = _bar(stamp, *row)


def test_retrace_after_rejected_touch_is_counted_with_its_confirmation() -> None:
    bars = _market(4)
    morning = datetime(2026, 1, 15, 3, 0, tzinfo=NY)
    # 5-minute bar 03:00: touches 115 (k=0.5 above the Asia high), closes back at 114.
    _inject(bars, morning, [(105, 115.5, 105, 114)] + [(114, 114.2, 113.8, 114)] * 4)
    # 03:05 bar confirms (closes below the level); 03:10 onward falls far below.
    _inject(bars, morning + timedelta(minutes=5), [(114, 114.1, 113.5, 113.6)] * 5)
    _inject(bars, morning + timedelta(minutes=10), [(113, 113, 98, 98)] * 5)
    events = level_events(bars)
    hit = [
        e
        for e in events
        if e.day == "2026-01-15" and e.side == "up" and e.level == 0.5 and e.kind == "named"
    ]
    assert len(hit) == 1
    event = hit[0]
    assert event.outcome == "retrace"
    features = dict(event.features)
    assert features["touch_bar_close"] == "rejected"
    assert features["confirm_bar_close"] == "rejects"
    assert features["session"] == "london_kz"
    # The deeper level k=1.0 (120) was never reached that day.
    assert not [e for e in events if e.day == "2026-01-15" and e.side == "up" and e.level == 1.0]


def test_breakout_continues_when_price_extends_first() -> None:
    bars = _market(4)
    morning = datetime(2026, 1, 15, 8, 0, tzinfo=NY)
    _inject(bars, morning, [(105, 116.2, 105, 116)] + [(116, 116.2, 115.8, 116)] * 4)
    _inject(bars, morning + timedelta(minutes=5), [(116, 116.5, 115.8, 116.4)] * 5)
    _inject(bars, morning + timedelta(minutes=10), [(117, 140, 117, 139)] * 5)
    events = level_events(bars)
    event = next(
        e
        for e in events
        if e.day == "2026-01-15" and e.side == "up" and e.level == 0.5 and e.kind == "named"
    )
    assert event.outcome == "continue"
    assert dict(event.features)["touch_bar_close"] == "accepted"


def test_only_first_touch_per_day_side_level_and_summary_shape() -> None:
    bars = _market(4)
    morning = datetime(2026, 1, 15, 3, 0, tzinfo=NY)
    _inject(bars, morning, [(105, 115.5, 105, 114)] * 5)
    _inject(bars, morning + timedelta(minutes=60), [(105, 115.5, 105, 114)] * 5)
    events = level_events(bars)
    firsts = [
        e
        for e in events
        if e.day == "2026-01-15" and e.side == "up" and e.level == 0.5 and e.kind == "named"
    ]
    assert len(firsts) == 1
    summary = summarise_events(events)
    assert summary["events"] == len(events)
    named = summary["named"]  # type: ignore[index]
    assert "k=0.5/up" in named and "k=0.5/both" in named
    assert named["k=0.5/up"]["touches"] >= 1


def test_fade_edge_is_about_zero_when_the_nearer_barrier_explains_the_rate() -> None:
    from trader.lab.ostdv_events import LevelEvent, _fade_edge

    def event(outcome: str, offset: float) -> LevelEvent:
        return LevelEvent("2026-01-15", 2026, "up", 1.0, "named", outcome, offset, ())

    # Confirm bar closed 0.5 ATR back inside: a fair random walk retraces 3 times in 4.
    events = [event("retrace", -0.5)] * 3 + [event("continue", -0.5)]
    assert abs(_fade_edge(events) or 0.0) < 1e-9
    # Same 75% rate but closing right at the level (offset 0) would be a genuine edge.
    assert (_fade_edge([event("retrace", 0.0)] * 3 + [event("continue", 0.0)]) or 0.0) > 0.4
    assert _fade_edge([]) is None
