"""Persist closed Capital.com bid/ask minute bars to an idempotent Parquet lake."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from trader.integrations.capital_com.stream import OhlcUpdate

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

    def upsert_many(self, items: list[tuple[str, OhlcUpdate]]) -> int:
        """Merge a closed update batch and atomically replace affected month files."""
        grouped: dict[Path, list[tuple[str, OhlcUpdate]]] = defaultdict(list)
        for symbol, update in items:
            minute = self._minute(update.timestamp)
            if update.price_type not in {"bid", "ask"}:
                raise ValueError("recorder side must be bid or ask")
            if update.source not in _SOURCE_RANK:
                raise ValueError("unsupported recorder source")
            grouped[self._path(symbol, minute)].append((symbol, update))
        written = 0
        with self._lock:
            for path, batch in grouped.items():
                prior = pq.read_table(path).to_pylist() if path.exists() else []
                rows: dict[tuple[str, datetime, str], dict[str, Any]] = {}
                for row in prior:
                    key = (row["epic"], row["ts_utc"], row["side"])
                    rows[key] = row
                changed_keys: set[tuple[str, datetime, str]] = set()
                for _symbol, update in batch:
                    minute = self._minute(update.timestamp)
                    key = (update.epic, minute, update.price_type)
                    old = rows.get(key)
                    if old and _SOURCE_RANK[old["source"]] > _SOURCE_RANK[update.source]:
                        continue
                    rows[key] = {
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
                    changed_keys.add(key)
                for epic, minute, _side in changed_keys:
                    bid = rows.get((epic, minute, "bid"))
                    ask = rows.get((epic, minute, "ask"))
                    spread = ask["close"] - bid["close"] if bid and ask else None
                    if spread is not None and spread < 0:
                        raise ValueError("recorder ask close is below bid close")
                    if bid:
                        bid["spread_close"] = spread
                    if ask:
                        ask["spread_close"] = spread
                    written += 1
                path.parent.mkdir(parents=True, exist_ok=True)
                table = pa.Table.from_pylist(
                    sorted(
                        rows.values(),
                        key=lambda row: (row["ts_utc"], row["epic"], row["side"]),
                    ),
                    schema=_SCHEMA,
                )
                descriptor, name = tempfile.mkstemp(
                    prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent
                )
                os.close(descriptor)
                temporary = Path(name)
                try:
                    pq.write_table(table, temporary)
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
        return written


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
    ) -> None:
        self._store = store
        self._symbols = dict(symbols_by_epic)
        self._status_path = Path(status_path)
        self._clock = clock
        self._feed_adapters = feed_adapters
        self._pending: dict[tuple[str, datetime, str], _BufferedUpdate] = {}
        self._last_tick_by_epic: dict[str, datetime] = {}
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
            "last_liveness_at": None,
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

    def _commit(self, updates: list[_BufferedUpdate]) -> None:
        if not updates:
            return
        self._store.upsert_many([(item.symbol, item.update) for item in updates])
        self._status["last_closed_bar_at"] = max(
            item.update.timestamp.astimezone(UTC).replace(second=0, microsecond=0)
            for item in updates
        ).isoformat()
        adapter = self._feed_adapters
        derived_callback = getattr(adapter, "on_closed_bar", None)
        callback = derived_callback
        if not callable(callback):
            callback = getattr(adapter, "on_bar_close", None)
        if callable(callback):
            for item in updates:
                stamp = item.update.timestamp.astimezone(UTC).replace(second=0, microsecond=0)
                bar_id = f"{item.update.epic}:{stamp.isoformat()}"
                if callable(derived_callback):
                    callback(bar_id, stamp)
                else:
                    callback("1M", bar_id, stamp)

    def register_feed_adapters(self, adapters: Any | None) -> None:
        """Attach the shared runtime trigger adapter after construction."""
        with self._lock:
            self._feed_adapters = adapters

    def on_update(self, update: OhlcUpdate) -> None:
        """Keep partial values in memory until their minute closes or REST confirms it."""
        symbol = self._symbols.get(update.epic)
        if symbol is None:
            return
        minute = self._key(update)[1]
        current_minute = self._clock().astimezone(UTC).replace(second=0, microsecond=0)
        with self._lock:
            updated_at = self._clock().astimezone(UTC)
            self._status["last_update_at"] = updated_at.isoformat()
            self._status["ticks_received"] += 1
            self._status["last_tick_at"] = updated_at.isoformat()
            self._last_tick_by_epic[update.epic] = updated_at
            closed: list[_BufferedUpdate] = []
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
                threshold = stale_after.get(epic, 180.0)
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
            self._status["status"] = (
                "healthy"
                if (ping_reply_ok is True and market_status != "unknown" and not stale_epics)
                else "degraded"
            )
            self._write_status()

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
