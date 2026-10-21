"""Read-only lake feed for the gated Tai demo runtime.

The recorder worker owns the Capital.com stream.  The API process therefore tails
the worker's immutable Parquet output instead of registering a callback on a
second, unconnected recorder instance.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from trader.experiments.tai_wiring import TaiMinuteBar

LOGGER = logging.getLogger(__name__)


class TaiMinuteConsumer(Protocol):
    """Small adapter boundary used by the lake tailer and fixture tests."""

    def on_minute_bar(self, minute: TaiMinuteBar) -> object | None: ...

    def mark_feed_stale(self) -> None: ...

    def mark_feed_healthy(self) -> None: ...


def _utc_minute(value: object) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("lake timestamp must be timezone-aware")
    return stamp.astimezone(UTC).replace(second=0, microsecond=0)


def _lake_paths(lake_root: Path, epic: str) -> tuple[Path, ...]:
    """Find the same configured-epic and symbol-alias files as ``from_lake``."""
    paths = tuple((lake_root / epic / "1m_capital").glob("**/*.parquet"))
    if paths:
        return paths
    return tuple(lake_root.glob("*/1m_capital/*/*.parquet"))


def read_closed_lake_minutes(
    lake_root: str | Path,
    epic: str,
    *,
    before: datetime,
) -> tuple[TaiMinuteBar, ...]:
    """Read complete bid/ask mid minutes strictly before ``before``.

    A minute is emitted only when both sides exist.  Applying the closed-minute
    boundary before constructing the output keeps the currently forming minute
    out of the runtime even when one side has already been persisted.
    """
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - supplied by the runtime image
        raise RuntimeError("pyarrow is required for the Tai lake feed") from exc

    boundary = _utc_minute(before)
    accepted_epics = {epic}
    if epic in {"NSXUSD", "NAS100", "US100"}:
        accepted_epics.update({"NSXUSD", "NAS100", "US100"})
    paired: dict[datetime, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for path in _lake_paths(Path(lake_root), epic):
        try:
            rows = pq.read_table(
                path,
                columns=["epic", "ts_utc", "side", "open", "high", "low", "close"],
            ).to_pylist()
        except (OSError, ValueError):
            continue
        for row in rows:
            try:
                stamp = _utc_minute(row["ts_utc"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if stamp >= boundary:
                continue
            side = row.get("side")
            recorded_epic = row.get("epic")
            if side not in {"bid", "ask"}:
                continue
            if recorded_epic and recorded_epic not in accepted_epics:
                continue
            paired[stamp][str(side)] = row

    result: list[TaiMinuteBar] = []
    for stamp in sorted(paired):
        sides = paired[stamp]
        bid, ask = sides.get("bid"), sides.get("ask")
        if bid is None or ask is None:
            continue
        try:
            values = tuple(
                Decimal(str((bid[name] + ask[name]) / 2))
                for name in ("open", "high", "low", "close")
            )
            result.append(TaiMinuteBar(stamp, epic, values[0], values[1], values[2], values[3]))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    return tuple(result)


def _normalise_seed(value: datetime | None) -> datetime | None:
    return None if value is None else _utc_minute(value)


class TaiLakeTailer:
    """Poll a recorder lake and deliver each closed minute at most once."""

    def __init__(
        self,
        lake_root: str | Path,
        epic: str,
        consumer: TaiMinuteConsumer,
        *,
        interval_seconds: float = 20.0,
        stale_after_seconds: float = 300.0,
        clock: Callable[[], datetime] | None = None,
        market_open: Callable[[datetime], bool] | None = None,
        seed_after: datetime | None = None,
    ) -> None:
        if not epic.strip():
            raise ValueError("epic must not be empty")
        if interval_seconds <= 0 or stale_after_seconds <= 0:
            raise ValueError("tailer intervals must be positive")
        self.lake_root = Path(lake_root)
        self.epic = epic.strip()
        self.consumer = consumer
        self.interval_seconds = interval_seconds
        self.stale_after_seconds = stale_after_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._market_open = market_open or (lambda _now: True)
        now = _utc_minute(self._clock())
        self._last_seen_minute = _normalise_seed(seed_after)
        self._last_progress_at = now
        self._stale = False
        self._polls = 0
        self._last_step_boundary: datetime | None = None

    @property
    def last_seen_minute(self) -> datetime | None:
        return self._last_seen_minute

    @property
    def stale(self) -> bool:
        return self._stale

    def poll_once(self, now: datetime | None = None) -> int:
        """Feed new closed minutes and emit one bounded timing line per minute."""
        started_at = datetime.now(UTC)
        started = time.monotonic()
        try:
            return self._poll_once(now)
        finally:
            ended_at = datetime.now(UTC)
            boundary = ended_at.replace(second=0, microsecond=0)
            if self._last_step_boundary != boundary:
                self._last_step_boundary = boundary
                LOGGER.info(
                    "tai feed step: boundary_minute=%s step_start=%s step_end=%s "
                    "step_duration_seconds=%.3f thread=%s",
                    boundary.isoformat(),
                    started_at.isoformat(),
                    ended_at.isoformat(),
                    time.monotonic() - started,
                    threading.current_thread().name,
                )

    def _poll_once(self, now: datetime | None = None) -> int:
        """Feed new closed minutes and update the bounded stale guard."""
        current = _utc_minute(now or self._clock())
        minutes = read_closed_lake_minutes(self.lake_root, self.epic, before=current)
        pending = tuple(
            minute
            for minute in minutes
            if self._last_seen_minute is None or minute.at > self._last_seen_minute
        )
        for minute in pending:
            self.consumer.on_minute_bar(minute)
            self._last_seen_minute = minute.at
        self._polls += 1
        if pending or self._polls == 1 or self._polls % 15 == 0:
            # One compact line on the first poll, whenever minutes are fed, and about every
            # five minutes otherwise, so a silent feed is visible in the log.
            LOGGER.info(
                "tai feed poll=%d lake_minutes=%d last_lake_minute=%s fed=%d last_seen=%s stale=%s",
                self._polls,
                len(minutes),
                minutes[-1].at.isoformat() if minutes else "none",
                len(pending),
                self._last_seen_minute.isoformat() if self._last_seen_minute else "none",
                self._stale,
            )
        if pending:
            self._last_progress_at = current
            self._stale = False
            healthy = getattr(self.consumer, "mark_feed_healthy", None)
            if callable(healthy):
                healthy()
        try:
            is_open = bool(self._market_open(current))
        except Exception:  # noqa: BLE001 - a schedule failure must fail safe
            is_open = False
        elapsed = (current - self._last_progress_at).total_seconds()
        if is_open and elapsed >= self.stale_after_seconds:
            if not self._stale:
                self._stale = True
                stale = getattr(self.consumer, "mark_feed_stale", None)
                if callable(stale):
                    stale()
        return len(pending)


__all__ = ["TaiLakeTailer", "TaiMinuteConsumer", "read_closed_lake_minutes"]
