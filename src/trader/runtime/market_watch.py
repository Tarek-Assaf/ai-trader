"""Tell the owner once when a market closes and once when it opens (broker status)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from trader.data.market_hours import MarketHours

LOGGER = logging.getLogger(__name__)
OWNER_ZONE = ZoneInfo("Asia/Riyadh")


def _stamp(value: datetime) -> str:
    return value.astimezone(OWNER_ZONE).strftime("%a %H:%M Riyadh")


class MarketWatcher:
    """Poll the broker's marketStatus; alert only on a change (never on the first look)."""

    def __init__(self, client: Any, epics: list[str], alert: Callable[[str], None]) -> None:
        self._client = client
        self._epics = list(epics)
        self._alert = alert
        self._last: dict[str, bool] = {}

    def poll(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        for epic in self._epics:
            try:
                details = self._client.market_details(epic)
                snapshot = details.get("snapshot") if hasattr(details, "get") else None
                status = str(snapshot.get("marketStatus", "")).upper() if snapshot else ""
            except Exception:  # noqa: BLE001 - a lookup failure must not raise alerts
                LOGGER.warning("market status lookup failed epic=%s", epic)
                continue
            if status not in {"TRADEABLE", "CLOSED"}:
                continue
            is_open = status == "TRADEABLE"
            previous = self._last.get(epic)
            self._last[epic] = is_open
            if previous is None or previous == is_open:
                continue
            self._alert(self._message(epic, is_open, details, now))

    @staticmethod
    def _message(epic: str, is_open: bool, details: Any, now: datetime) -> str:
        try:
            hours = MarketHours.from_market_details(details)
        except Exception:  # noqa: BLE001 - hours are an extra
            hours = None
        if is_open:
            text = f"MARKET OPEN {epic} at {_stamp(now)}. Bot is trading it again."
            try:
                if hours is not None:
                    text += f"\nNext close: {_stamp(hours.next_close(now))}"
            except Exception:  # noqa: BLE001
                pass
            return text
        text = f"MARKET CLOSED {epic} at {_stamp(now)}. No new entries until it reopens."
        try:
            if hours is not None:
                text += f"\nReopens: {_stamp(hours.next_open(now))}"
        except Exception:  # noqa: BLE001
            pass
        return text + "\nOpen positions keep their stop and targets."


class DiskWatcher:
    """Alert once when the disk passes a threshold (a full disk stops the database)."""

    def __init__(
        self,
        alert: Callable[[str], None],
        *,
        path: str = "/",
        threshold_pct: float = 80.0,
        repeat_seconds: float = 6 * 3600,
        usage: Callable[[str], Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import shutil
        import time

        self._alert = alert
        self._path = path
        self._threshold = threshold_pct
        self._repeat = repeat_seconds
        self._usage = usage or shutil.disk_usage
        self._clock = clock or time.monotonic
        self._last_alert: float | None = None

    def poll(self) -> None:
        try:
            total, used, free = self._usage(self._path)[:3]
        except OSError:
            return
        pct = used / total * 100 if total else 0.0
        if pct < self._threshold:
            self._last_alert = None
            return
        now = self._clock()
        if self._last_alert is not None and now - self._last_alert < self._repeat:
            return
        self._last_alert = now
        self._alert(
            f"DISK {pct:.0f}% FULL, {free / 1e9:.1f} GB free. A full disk stops the "
            "database and the bot. Remove old images: docker image prune -a -f"
        )
