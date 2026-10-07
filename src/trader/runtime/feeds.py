"""Market and calendar trigger adapters for the core runtime."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from trader.runtime.composition import CycleTrigger, ScheduleCycleSource


class RuntimeFeedAdapters:
    """Translate recorder/calendar callbacks into stable cycle triggers."""

    _BAR_INTERVALS = frozenset({"1M", "3M", "5M"})

    def __init__(
        self,
        source: ScheduleCycleSource,
        *,
        alert: Callable[[str], None] | None = None,
    ) -> None:
        self._source = source
        self._alert = alert or (lambda _message: None)

    def on_bar_close(self, interval: str, bar_id: str, at: datetime) -> None:
        """Emit one deterministic trigger for a closed 1M/3M/5M bar."""
        normalized = interval.strip().upper()
        if normalized not in self._BAR_INTERVALS:
            raise ValueError("bar interval must be one of 1M, 3M or 5M")
        if not bar_id.strip():
            raise ValueError("bar id must not be empty")
        self._emit(CycleTrigger("bar_close", f"bar:{normalized}:{bar_id}", at))

    def on_session_open(self, session_id: str, at: datetime) -> None:
        if not session_id.strip():
            raise ValueError("session id must not be empty")
        self._emit(CycleTrigger("session_open", f"session:{session_id}", at))

    def on_news_event(self, event_id: str, at: datetime) -> None:
        if not event_id.strip():
            raise ValueError("news event id must not be empty")
        self._emit(CycleTrigger("news_event", f"news:{event_id}", at))

    def missing_feed(self, feed_name: str) -> None:
        """Alert once per adapter callback when a configured feed is absent."""
        name = feed_name.strip()
        if not name:
            raise ValueError("feed name must not be empty")
        self._alert(f"runtime_feed_unavailable:{name}")

    def _emit(self, trigger: CycleTrigger) -> None:
        try:
            self._source.emit(trigger)
        except Exception:
            self._alert("runtime_feed_emit_failed")


__all__ = ["RuntimeFeedAdapters"]
