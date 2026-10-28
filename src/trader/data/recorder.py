"""Persist closed Capital.com bid/ask minute bars to an idempotent Parquet lake."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from trader.data.market_hours import MarketHours
from trader.integrations.capital_com.stream import OhlcUpdate

_LOG = logging.getLogger(__name__)

_SCHEMA = pa.schema(
    [
        ("epic", pa.string()),
        ("ts_utc", pa.timestamp("us", tz="UTC")),
        ("side", pa.string()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("spread_close", pa.float64()),
        ("source", pa.string()),
        ("lineage_tag", pa.string()),
    ]
)
_SOURCE_RANK = {"capital_ws": 1, "capital_rest": 2}
_MAX_REJECTED_CANDLES = 1_000_000
_REJECT_ASK_BELOW_BID = "ask_close_below_bid_close"
_REJECT_TIMESTAMP = "timestamp_not_timezone_aware"
_REJECT_SIDE = "invalid_side"
_REJECT_SOURCE = "invalid_source"
_STALL_AFTER_SECONDS = 60
_STALL_REPEAT_SECONDS = 30 * 60
_REJECTION_LOG_INTERVAL_SECONDS = 60.0
_SLOW_TASK_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class _BufferedUpdate:
    update: OhlcUpdate
    symbol: str


class ParquetCandleStore:
    """Atomically upsert monthly per-side bars by (epic, UTC minute, side)."""

    def __init__(self, lake_root: str | Path) -> None:
        self._root = Path(lake_root)
        self._lock = threading.RLock()

    @staticmethod
    def _minute(stamp: datetime) -> datetime:
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("recorder timestamps must be timezone-aware")
        return stamp.astimezone(UTC).replace(second=0, microsecond=0)

    def _path(self, symbol: str, minute: datetime) -> Path:
        return (
            self._root
            / symbol
            / "1m_capital"
            / f"{minute.year:04d}"
            / f"{minute.month:02d}.parquet"
        )

    def latest_watermarks(self, epics: set[str]) -> dict[tuple[str, str], datetime]:
        """Read the latest persisted minute for each epic and side at process startup."""
        latest: dict[tuple[str, str], datetime] = {}
        for path in self._root.glob("*/1m_capital/*/*.parquet"):
            try:
                rows = pq.read_table(path, columns=["epic", "ts_utc", "side"]).to_pylist()
            except (OSError, pa.ArrowException) as exc:
                raise RuntimeError("recorder Parquet watermark file cannot be read") from exc
            for row in rows:
                epic, side, stamp = row["epic"], row["side"], row["ts_utc"]
                if epic in epics and side in {"bid", "ask"}:
                    key = (epic, side)
                    if key not in latest or stamp > latest[key]:
                        latest[key] = stamp
        return latest

    def upsert_many(
        self,
        items: list[tuple[str, OhlcUpdate]],
        *,
        on_reject: Callable[[str, OhlcUpdate | None], None] | None = None,
    ) -> int:
        """Merge a closed update batch and atomically replace affected month files."""

        def reject(reason: str, update: OhlcUpdate | None = None) -> None:
            if not callable(on_reject):
                return
            try:
                on_reject(reason, update)
            except TypeError:
                # Preserve compatibility with older one-argument callbacks.
                on_reject(reason)  # type: ignore[call-arg]

        grouped: dict[Path, list[tuple[str, OhlcUpdate]]] = defaultdict(list)
        for symbol, update in items:
            try:
                minute = self._minute(update.timestamp)
            except ValueError:
                reject(_REJECT_TIMESTAMP, update)
                continue
            if update.price_type not in {"bid", "ask"}:
                reject(_REJECT_SIDE, update)
                continue
            if update.source not in _SOURCE_RANK:
                reject(_REJECT_SOURCE, update)
                continue
            grouped[self._path(symbol, minute)].append((symbol, update))
        written = 0
        with self._lock:
            for path, batch in grouped.items():
                stage_started = time.monotonic()
                prior_table = (
                    pq.read_table(path)
                    if path.exists()
                    else pa.Table.from_pylist([], schema=_SCHEMA)
                )
                self._log_slow_stage("read", stage_started, path, prior_table.num_rows)

                pairs = {(update.epic, self._minute(update.timestamp)) for _symbol, update in batch}
                pair_mask = pc.equal(prior_table["epic"], pa.scalar("__never__"))
                timestamp_type = _SCHEMA.field("ts_utc").type
                for epic, minute in pairs:
                    current = pc.and_(
                        pc.equal(prior_table["epic"], pa.scalar(epic)),
                        pc.equal(
                            prior_table["ts_utc"],
                            pa.scalar(minute, type=timestamp_type),
                        ),
                    )
                    pair_mask = pc.or_(pair_mask, current)

                stage_started = time.monotonic()
                relevant = prior_table.filter(pair_mask).to_pylist()
                untouched = prior_table.filter(pc.invert(pair_mask))
                self._log_slow_stage("select", stage_started, path, prior_table.num_rows)
                rows: dict[tuple[str, datetime, str], dict[str, Any]] = {}
                for row in relevant:
                    key = (row["epic"], row["ts_utc"], row["side"])
                    rows[key] = row
                changed_keys: set[tuple[str, datetime, str]] = set()
                for _symbol, update in sorted(
                    batch,
                    key=lambda item: (
                        self._minute(item[1].timestamp),
                        0 if item[1].price_type == "bid" else 1,
                    ),
                ):
                    minute = self._minute(update.timestamp)
                    key = (update.epic, minute, update.price_type)
                    old = rows.get(key)
                    if old and _SOURCE_RANK[old["source"]] > _SOURCE_RANK[update.source]:
                        continue
                    candidate: dict[str, Any] = {
                        "epic": update.epic,
                        "ts_utc": minute,
                        "side": update.price_type,
                        "open": update.open,
                        "high": update.high,
                        "low": update.low,
                        "close": update.close,
                        "spread_close": None,
                        "source": update.source,
                        "lineage_tag": "capital_com_1m_recorder_v1",
                    }
                    other_side = "bid" if update.price_type == "ask" else "ask"
                    other = rows.get((update.epic, minute, other_side))
                    spread = (
                        candidate["close"] - other["close"]
                        if update.price_type == "ask" and other
                        else other["close"] - candidate["close"]
                        if update.price_type == "bid" and other
                        else None
                    )
                    if spread is not None and spread < 0:
                        reject(_REJECT_ASK_BELOW_BID, update)
                        continue
                    rows[key] = candidate
                    changed_keys.add(key)
                if not changed_keys:
                    continue
                pair_keys = {(epic, minute) for epic, minute, _side in rows}
                for epic, minute in pair_keys:
                    bid = rows.get((epic, minute, "bid"))
                    ask = rows.get((epic, minute, "ask"))
                    if bid and ask and ask["close"] < bid["close"]:
                        rows.pop((epic, minute, "ask"), None)
                        reject(_REJECT_ASK_BELOW_BID)
                for epic, minute, _side in changed_keys:
                    bid = rows.get((epic, minute, "bid"))
                    ask = rows.get((epic, minute, "ask"))
                    spread = ask["close"] - bid["close"] if bid and ask else None
                    if spread is not None and spread < 0:
                        continue
                    if bid:
                        bid["spread_close"] = spread
                    if ask:
                        ask["spread_close"] = spread
                    written += 1
                path.parent.mkdir(parents=True, exist_ok=True)
                stage_started = time.monotonic()
                replacement = pa.Table.from_pylist(list(rows.values()), schema=_SCHEMA)
                table = pa.concat_tables([untouched, replacement])
                indices = pc.sort_indices(
                    table,
                    sort_keys=[
                        ("ts_utc", "ascending"),
                        ("epic", "ascending"),
                        ("side", "ascending"),
                    ],
                )
                table = pc.take(table, indices)
                self._log_slow_stage("merge", stage_started, path, table.num_rows)
                descriptor, name = tempfile.mkstemp(
                    prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent
                )
                os.close(descriptor)
                temporary = Path(name)
                try:
                    stage_started = time.monotonic()
                    pq.write_table(table, temporary)
                    temporary.replace(path)
                    self._log_slow_stage("write", stage_started, path, table.num_rows)
                finally:
                    temporary.unlink(missing_ok=True)
        return written

    @staticmethod
    def _log_slow_stage(stage: str, started: float, path: Path, rows: int) -> None:
        duration = time.monotonic() - started
        if duration >= _SLOW_TASK_SECONDS:
            _LOG.warning(
                "Capital.com Parquet task slow: stage=%s duration_seconds=%.3f "
                "rows=%d file=%s thread=%s",
                stage,
                duration,
                rows,
                path.name,
                threading.current_thread().name,
            )


class CapitalDataRecorder:
    """Buffer partial WebSocket bars and publish closed or REST-reconciled minutes."""

    def __init__(
        self,
        store: ParquetCandleStore,
        symbols_by_epic: dict[str, str],
        status_path: str | Path,
        *,
        clock: Any = lambda: datetime.now(UTC),
        feed_adapters: Any | None = None,
        closed_minute_callback: Callable[[str, datetime, float, float, float, float], None]
        | None = None,
        on_alert: Callable[[str], None] | None = None,
        market_hours_by_epic: dict[str, MarketHours | None] | None = None,
    ) -> None:
        self._store = store
        self._symbols = dict(symbols_by_epic)
        self._status_path = Path(status_path)
        self._clock = clock
        self._feed_adapters = feed_adapters
        self._closed_minute_callback = closed_minute_callback
        self._on_alert = on_alert
        self._market_hours_by_epic = dict(market_hours_by_epic or {})
        self._closed_mid_pending: dict[tuple[str, datetime], dict[str, OhlcUpdate]] = {}
        self._pending: dict[tuple[str, datetime, str], _BufferedUpdate] = {}
        self._queued_updates: SimpleQueue[OhlcUpdate] = SimpleQueue()
        self._last_tick_by_epic: dict[str, datetime] = {}
        self._last_closed_bar_at: datetime | None = None
        self._feed_stalled = False
        self._stall_since: datetime | None = None
        self._last_stall_alert_at: datetime | None = None
        self._rejection_log_at: dict[str, datetime] = {}
        self._started_at = self._clock().astimezone(UTC)
        self._lock = threading.RLock()
        self._status: dict[str, Any] = {
            "configured": True,
            "status": "starting",
            "ping_reply_ok": None,
            "market_status": "unknown",
            "market_status_source": "unknown",
            "expected_open": None,
            "stale_epics": [],
            "ticks_received": 0,
            "last_tick_at": None,
            "reconnect_count": 0,
            "last_reconnect_reason": None,
            "last_update_at": None,
            "last_closed_bar_at": None,
            "feed_stalled": False,
            "minutes_since_last_bar": None,
            "last_liveness_at": None,
            "rejected_candles": 0,
            "last_reject_reason": None,
        }
        self._write_status()

    def _write_status(self) -> None:
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=".recorder-status.", suffix=".tmp", dir=self._status_path.parent
        )
        os.close(descriptor)
        temporary = Path(name)
        try:
            temporary.write_text(json.dumps(self._status, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(self._status_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _key(update: OhlcUpdate) -> tuple[str, datetime, str]:
        if update.timestamp.tzinfo is None or update.timestamp.utcoffset() is None:
            raise ValueError("recorder timestamps must be timezone-aware")
        return (
            update.epic,
            update.timestamp.astimezone(UTC).replace(second=0, microsecond=0),
            update.price_type,
        )

    @staticmethod
    def _reject_reason(update: OhlcUpdate) -> str | None:
        if update.timestamp.tzinfo is None or update.timestamp.utcoffset() is None:
            return _REJECT_TIMESTAMP
        if update.price_type not in {"bid", "ask"}:
            return _REJECT_SIDE
        if update.source not in _SOURCE_RANK:
            return _REJECT_SOURCE
        return None

    def _commit(self, updates: list[_BufferedUpdate]) -> None:
        if not updates:
            return
        started_at = datetime.now(UTC)
        started = time.monotonic()
        try:
            self._commit_updates(updates)
        finally:
            ended_at = datetime.now(UTC)
            boundary = ended_at.replace(second=0, microsecond=0)
            _LOG.info(
                "Capital.com bar commit: boundary_minute=%s commit_start=%s commit_end=%s "
                "commit_duration_seconds=%.3f updates=%s thread=%s",
                boundary.isoformat(),
                started_at.isoformat(),
                ended_at.isoformat(),
                time.monotonic() - started,
                len(updates),
                threading.current_thread().name,
            )

    def _commit_updates(self, updates: list[_BufferedUpdate]) -> None:
        if not updates:
            return
        accepted: list[_BufferedUpdate] = []
        for item in updates:
            reason = self._reject_reason(item.update)
            if reason is None:
                accepted.append(item)
            else:
                self._record_rejection(reason)
        if not accepted:
            return
        self._store.upsert_many(
            [(item.symbol, item.update) for item in accepted], on_reject=self._record_rejection
        )
        latest_closed = max(
            item.update.timestamp.astimezone(UTC).replace(second=0, microsecond=0)
            for item in accepted
        )
        self._last_closed_bar_at = latest_closed
        self._status["last_closed_bar_at"] = latest_closed.isoformat()
        in_maintenance_break = self._suppress_stall_alerts_if_break(self._clock().astimezone(UTC))
        if self._feed_stalled and not in_maintenance_break:
            self._feed_stalled = False
            self._stall_since = None
            self._last_stall_alert_at = None
            self._status["feed_stalled"] = False
            self._status["minutes_since_last_bar"] = 0.0
            self._emit_alert(f"Capital.com feed recovered; last bar {latest_closed.isoformat()}")
        callback_mid = self._closed_minute_callback
        if callable(callback_mid):
            for item in accepted:
                stamp = item.update.timestamp.astimezone(UTC).replace(second=0, microsecond=0)
                self._closed_mid_pending.setdefault((item.symbol, stamp), {})[
                    item.update.price_type
                ] = item.update
            for (symbol, stamp), sides in list(self._closed_mid_pending.items()):
                bid, ask = sides.get("bid"), sides.get("ask")
                if bid is None or ask is None:
                    continue
                self._closed_mid_pending.pop((symbol, stamp), None)
                try:
                    callback_mid(
                        symbol,
                        stamp,
                        (bid.open + ask.open) / 2,
                        (bid.high + ask.high) / 2,
                        (bid.low + ask.low) / 2,
                        (bid.close + ask.close) / 2,
                    )
                except Exception:  # noqa: BLE001 - data listeners must not stop persistence
                    continue
        adapter = self._feed_adapters
        derived_callback = getattr(adapter, "on_closed_bar", None)
        callback = derived_callback
        if not callable(callback):
            callback = getattr(adapter, "on_bar_close", None)
        if callable(callback):
            for item in accepted:
                stamp = item.update.timestamp.astimezone(UTC).replace(second=0, microsecond=0)
                bar_id = f"{item.update.epic}:{stamp.isoformat()}"
                if callable(derived_callback):
                    callback(bar_id, stamp)
                else:
                    callback("1M", bar_id, stamp)

    def _record_rejection(self, reason: str, update: OhlcUpdate | None = None) -> None:
        """Record a bounded, secret-free data-quality rejection."""
        with self._lock:
            self._status["rejected_candles"] = min(
                int(self._status.get("rejected_candles", 0)) + 1,
                _MAX_REJECTED_CANDLES,
            )
            self._status["last_reject_reason"] = reason[:80]
            now = self._clock().astimezone(UTC)
            previous = self._rejection_log_at.get(reason)
            if (
                previous is None
                or (now - previous).total_seconds() >= _REJECTION_LOG_INTERVAL_SECONDS
            ):
                self._rejection_log_at[reason] = now
                epic = update.epic if update is not None else "unknown"
                side = update.price_type if update is not None else "unknown"
                stamp = update.timestamp.isoformat() if update is not None else "unknown"
                _LOG.warning(
                    "Capital.com rejected candle: time=%s epic=%s side=%s reason=%s",
                    stamp,
                    epic,
                    side,
                    reason[:80],
                )

    def _emit_alert(self, message: str) -> None:
        callback = self._on_alert
        if not callable(callback):
            return
        try:
            callback(message[:240])
        except Exception:  # noqa: BLE001 - alerts must not stop recording
            _LOG.warning("Capital.com recorder alert delivery failed")

    def register_feed_adapters(self, adapters: Any | None) -> None:
        """Attach the shared runtime trigger adapter after construction."""
        with self._lock:
            self._feed_adapters = adapters

    def register_closed_minute_callback(
        self, callback: Callable[[str, datetime, float, float, float, float], None] | None
    ) -> None:
        """Attach a listener for complete bid/ask mid minute bars.

        The callback is invoked only when both sides of a closed minute are
        present.  It receives the configured symbol, UTC minute and OHLC mid
        values.  Callback failures are isolated from persistence.
        """
        with self._lock:
            self._closed_minute_callback = callback

    def _on_updates(self, updates: Iterable[OhlcUpdate]) -> None:
        """Buffer updates and commit all closed values in one store call."""
        current_minute = self._clock().astimezone(UTC).replace(second=0, microsecond=0)
        with self._lock:
            closed: list[_BufferedUpdate] = []
            for update in updates:
                symbol = self._symbols.get(update.epic)
                if symbol is None:
                    continue
                reason = self._reject_reason(update)
                if reason is not None:
                    self._record_rejection(reason, update)
                    continue
                updated_at = self._clock().astimezone(UTC)
                self._status["last_update_at"] = updated_at.isoformat()
                self._status["ticks_received"] += 1
                self._status["last_tick_at"] = updated_at.isoformat()
                self._last_tick_by_epic[update.epic] = updated_at
                minute = self._key(update)[1]
                for key, buffered in list(self._pending.items()):
                    if key[0] == update.epic and key[1] < minute:
                        closed.append(buffered)
                        del self._pending[key]
                key = self._key(update)
                prior = self._pending.get(key)
                if update.source == "capital_rest" and minute < current_minute:
                    closed.append(_BufferedUpdate(update, symbol))
                    self._pending.pop(key, None)
                elif minute < current_minute:
                    # A late closed WebSocket minute can be saved once it is known to be closed.
                    if prior is None or prior.update.source != "capital_rest":
                        closed.append(_BufferedUpdate(update, symbol))
                    self._pending.pop(key, None)
                elif (
                    prior is None
                    or prior.update.source != "capital_rest"
                    or update.source == "capital_rest"
                ):
                    self._pending[key] = _BufferedUpdate(update, symbol)
            self._commit(closed)
            self._write_status()

    def on_update(self, update: OhlcUpdate) -> None:
        """Keep partial values in memory until their minute closes or REST confirms it."""
        self._on_updates((update,))

    def enqueue_update(self, update: OhlcUpdate) -> None:
        """Queue a WebSocket update so the receive loop never writes Parquet."""
        self._queued_updates.put(update)

    def drain_updates(self, *, max_updates: int = 256) -> int:
        """Persist queued WebSocket updates in one bounded batch.

        The worker owns this drain, keeping the Capital.com receive thread
        responsive while retaining the synchronous ``on_update`` API used by
        unit tests and backfill reconciliation.
        """
        if max_updates <= 0:
            raise ValueError("max_updates must be positive")
        updates: list[OhlcUpdate] = []
        for _ in range(max_updates):
            try:
                updates.append(self._queued_updates.get_nowait())
            except Empty:
                break
        if updates:
            self._on_updates(updates)
        return len(updates)

    def on_backfill_batch(self, updates: Iterable[OhlcUpdate]) -> None:
        """Persist a REST backfill page/epic with one store upsert."""
        self._on_updates(updates)

    def on_reconnect(self, count: int, reason: str) -> None:
        """Expose stream failures with a safe reason while retrying in the worker."""
        with self._lock:
            self._status["reconnect_count"] = count
            self._status["last_reconnect_reason"] = reason
            self._status["status"] = "degraded"
            self._write_status()

    def set_unconfigured(self) -> None:
        """Report missing owner-provided credentials without failing ROLE=all startup."""
        with self._lock:
            self._status["configured"] = False
            self._status["status"] = "not_configured"
            self._write_status()

    def last_tick_at(self, epic: str) -> datetime | None:
        """Return the latest observed stream update for one broker epic."""
        with self._lock:
            return self._last_tick_by_epic.get(epic)

    def update_liveness(
        self,
        *,
        ping_reply_ok: bool | None,
        market_status: str,
        expected_open_by_epic: dict[str, bool | None] | None = None,
        market_status_source: str = "broker",
        stale_after_seconds_by_epic: dict[str, float] | None = None,
        market_hours_by_epic: dict[str, MarketHours | None] | None = None,
    ) -> None:
        """Flag stale prices only for epics currently expected to publish updates."""
        if not market_status or len(market_status) > 40:
            raise ValueError("market status must be a non-empty short string")
        if not market_status_source or len(market_status_source) > 120:
            raise ValueError("market status source must be a non-empty short string")
        now = self._clock().astimezone(UTC)
        expected = expected_open_by_epic or {}
        stale_after = stale_after_seconds_by_epic or {}
        stale_epics: list[str] = []
        with self._lock:
            for epic, is_open in expected.items():
                if is_open is not True:
                    continue
                last_tick = self._last_tick_by_epic.get(epic, self._started_at)
                threshold = stale_after.get(epic, 60.0)
                if (now - last_tick).total_seconds() >= threshold:
                    stale_epics.append(epic)
            any_open = any(value is True for value in expected.values())
            all_known_closed = bool(expected) and all(value is False for value in expected.values())
            self._status["ping_reply_ok"] = ping_reply_ok
            self._status["market_status"] = market_status
            self._status["market_status_source"] = market_status_source
            if any_open:
                expected_open = True
            elif all_known_closed:
                expected_open = False
            else:
                expected_open = None
            self._status["expected_open"] = expected_open
            self._status["stale_epics"] = sorted(stale_epics)
            self._status["last_liveness_at"] = now.isoformat()
            if market_hours_by_epic is not None:
                self._market_hours_by_epic = dict(market_hours_by_epic)
            self._update_feed_stall(now, market_status, expected)
            self._status["status"] = (
                "healthy"
                if (ping_reply_ok is True and market_status != "unknown" and not stale_epics)
                else "degraded"
            )
            self._write_status()

    def _update_feed_stall(
        self, now: datetime, market_status: str, expected_open: dict[str, bool | None]
    ) -> None:
        """Track a closed-bar stall and emit bounded stall/recovery alerts."""
        market_unknown = market_status == "unknown" or any(
            value is None for value in expected_open.values()
        )
        feed_expected = market_unknown or any(value is True for value in expected_open.values())
        last_bar = self._last_closed_bar_at
        age_seconds = (
            (now - last_bar).total_seconds()
            if last_bar is not None
            else (now - self._started_at).total_seconds()
        )
        age_seconds = max(0.0, age_seconds)
        self._status["minutes_since_last_bar"] = round(age_seconds / 60.0, 1)
        if self._suppress_stall_alerts_if_break(now):
            return
        stalled = feed_expected and age_seconds >= _STALL_AFTER_SECONDS
        was_stalled = self._feed_stalled
        self._feed_stalled = stalled
        self._status["feed_stalled"] = stalled
        if stalled and not was_stalled:
            self._stall_since = now
            self._last_stall_alert_at = now
            self._emit_alert(
                "Capital.com feed stalled since "
                f"{now.strftime('%H:%M')} UTC, "
                f"last bar {last_bar.isoformat() if last_bar else 'never'}"
            )
        elif stalled and self._last_stall_alert_at is not None:
            if (now - self._last_stall_alert_at).total_seconds() >= _STALL_REPEAT_SECONDS:
                self._last_stall_alert_at = now
                stall_time = (
                    self._stall_since.strftime("%H:%M")
                    if self._stall_since
                    else now.strftime("%H:%M")
                )
                self._emit_alert(
                    f"Capital.com feed stalled since {stall_time} UTC, "
                    f"last bar {last_bar.isoformat() if last_bar else 'never'}"
                )

    def _suppress_stall_alerts_if_break(self, now: datetime) -> bool:
        """Treat the scheduled market-hours break as expected feed silence."""
        schedules = self._market_hours_by_epic
        in_break = bool(schedules) and all(
            schedule is not None and schedule.is_break(now) for schedule in schedules.values()
        )
        if not in_break:
            return False
        self._feed_stalled = False
        self._stall_since = None
        self._last_stall_alert_at = None
        self._status["feed_stalled"] = False
        return True

    def watermarks(self) -> dict[tuple[str, str], datetime]:
        """Return persisted side watermarks for stream reconnect REST backfill."""
        latest = self._store.latest_watermarks(set(self._symbols))
        # Seed a side with the persisted timestamp of its pair when only one
        # side exists, avoiding an unbounded historical backfill on startup.
        for epic in self._symbols:
            known = [latest[(epic, side)] for side in ("bid", "ask") if (epic, side) in latest]
            if known:
                watermark = min(known)
                for side in ("bid", "ask"):
                    latest.setdefault((epic, side), watermark)
        return latest
