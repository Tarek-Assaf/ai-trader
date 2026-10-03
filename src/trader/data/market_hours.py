"""Broker-derived market sessions with explicit holiday and early-close overrides."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_MINUTES_PER_DAY = 24 * 60


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _minute_of_day(value: str) -> int:
    try:
        hour_text, minute_text = value.strip().split(":", maxsplit=1)
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, ValueError) as exc:
        raise ValueError("broker opening-hours time is malformed") from exc
    if hour == 24 and minute == 0:
        return _MINUTES_PER_DAY
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("broker opening-hours time is outside a day")
    return hour * 60 + minute


@dataclass(frozen=True, slots=True)
class MarketHours:
    """Weekly broker opening-hours schedule plus explicit date overrides."""

    zone: ZoneInfo
    weekly: dict[int, tuple[tuple[int, int], ...]]
    holidays: frozenset[date] = frozenset()
    early_closes: dict[date, time] | None = None

    @classmethod
    def from_market_details(
        cls,
        payload: object,
        *,
        holidays: tuple[date, ...] = (),
        early_closes: Mapping[date, time] | None = None,
        fallback_zone: str = "UTC",
    ) -> MarketHours:
        root = _as_mapping(payload)
        instrument = _as_mapping(root.get("instrument")) if root is not None else None
        opening = _as_mapping(instrument.get("openingHours")) if instrument is not None else None
        if opening is None:
            raise ValueError("Capital.com market details have no instrument openingHours")
        zone_name = opening.get("zone", fallback_zone)
        if not isinstance(zone_name, str) or not zone_name:
            raise ValueError("broker opening-hours timezone is missing")
        try:
            zone = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("broker opening-hours timezone is unknown") from exc

        weekly: dict[int, tuple[tuple[int, int], ...]] = {}
        for weekday, day_name in enumerate(_DAY_NAMES):
            raw_intervals = opening.get(day_name)
            if not isinstance(raw_intervals, list):
                raise ValueError(f"broker openingHours.{day_name} is malformed")
            intervals: list[tuple[int, int]] = []
            for raw_interval in raw_intervals:
                if not isinstance(raw_interval, str) or " - " not in raw_interval:
                    raise ValueError("broker opening-hours interval is malformed")
                start_text, end_text = raw_interval.split(" - ", maxsplit=1)
                start = _minute_of_day(start_text)
                end = _minute_of_day(end_text)
                if start == end == 0:
                    end = _MINUTES_PER_DAY
                elif end == 0:
                    end = _MINUTES_PER_DAY
                if start < end:
                    intervals.append((start, end))
                else:
                    intervals.append((start, _MINUTES_PER_DAY))
                    next_weekday = (weekday + 1) % 7
                    weekly.setdefault(next_weekday, ())
                    weekly[next_weekday] += ((0, end),)
            weekly[weekday] = tuple(sorted(intervals))
        return cls(
            zone=zone,
            weekly=weekly,
            holidays=frozenset(holidays),
            early_closes=dict(early_closes or {}),
        )

    def _intervals(self, day: date) -> tuple[tuple[int, int], ...]:
        if day in self.holidays:
            return ()
        intervals = self.weekly.get(day.weekday(), ())
        close_time = (self.early_closes or {}).get(day)
        if close_time is None:
            return intervals
        close_minute = close_time.hour * 60 + close_time.minute
        return tuple(
            (start, min(end, close_minute))
            for start, end in intervals
            if start < close_minute
        )

    def is_open(self, stamp: datetime) -> bool:
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("market-hours timestamps must be timezone-aware")
        local = stamp.astimezone(self.zone)
        minute = local.hour * 60 + local.minute
        return any(start <= minute < end for start, end in self._intervals(local.date()))

    def is_break(self, stamp: datetime) -> bool:
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("market-hours timestamps must be timezone-aware")
        local = stamp.astimezone(self.zone)
        if local.date() in self.holidays:
            return False
        intervals = self.weekly.get(local.weekday(), ())
        if len(intervals) < 2:
            return False
        minute = local.hour * 60 + local.minute
        return not any(start <= minute < end for start, end in self._intervals(local.date()))

    def next_open(self, stamp: datetime) -> datetime:
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("market-hours timestamps must be timezone-aware")
        local = stamp.astimezone(self.zone)
        if self.is_open(local):
            return local.astimezone(UTC)
        for offset in range(0, 367):
            day = local.date() + timedelta(days=offset)
            for start, _end in self._intervals(day):
                candidate = datetime.combine(day, time.min, self.zone) + timedelta(minutes=start)
                if candidate > local:
                    return candidate.astimezone(UTC)
        raise ValueError("no market opening found in the next year")

    def next_close(self, stamp: datetime) -> datetime:
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("market-hours timestamps must be timezone-aware")
        local = stamp.astimezone(self.zone)
        minute = local.hour * 60 + local.minute
        for start, end in self._intervals(local.date()):
            if start <= minute < end:
                return (
                    datetime.combine(local.date(), time.min, self.zone) + timedelta(minutes=end)
                ).astimezone(UTC)
        raise ValueError("market is closed at the requested time")

    def upcoming_holidays(self, stamp: datetime, days: int) -> list[date]:
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("market-hours timestamps must be timezone-aware")
        if days < 0:
            raise ValueError("days must be non-negative")
        today = stamp.astimezone(self.zone).date()
        last_day = today + timedelta(days=days)
        return sorted(day for day in self.holidays if today <= day <= last_day)


def resolve_market_open(
    broker_status: str,
    schedule: MarketHours | None,
    stamp: datetime,
    *,
    last_tick_at: datetime | None,
    stale_after_seconds: float,
) -> tuple[bool | None, str]:
    """Use broker status first, then broker hours, then the stale-feed fallback."""
    if broker_status == "TRADEABLE":
        return True, "broker"
    if broker_status == "CLOSED":
        return False, "broker"
    if schedule is None:
        return None, "unknown"
    if not schedule.is_open(stamp):
        return False, "schedule"
    if last_tick_at is not None:
        if last_tick_at.tzinfo is None or last_tick_at.utcoffset() is None:
            raise ValueError("last tick timestamp must be timezone-aware")
        age = (stamp.astimezone(UTC) - last_tick_at.astimezone(UTC)).total_seconds()
        if age >= stale_after_seconds:
            return False, "stale_feed"
    return True, "schedule"
