"""SQLite-backed research cache and cross-process request pacing."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class CacheEntry:
    value: str
    expires_at: float


class SQLiteResearchCache:
    """Small persistent TTL cache with an atomic per-source rate reservation."""

    def __init__(self, path: str, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._connection = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS research_cache "
            "(cache_key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL NOT NULL)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS research_rate "
            "(source TEXT PRIMARY KEY, next_allowed REAL NOT NULL)"
        )
        self._connection.commit()
        self._lock = threading.RLock()

    def get(self, key: str) -> str | None:
        """Return a live cache entry, deleting an expired entry."""
        now = self._clock()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT value, expires_at FROM research_cache WHERE cache_key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            value, expires_at = row
            if float(expires_at) <= now:
                self._connection.execute(
                    "DELETE FROM research_cache WHERE cache_key = ?", (key,)
                )
                return None
            return str(value)

    def set(self, key: str, value: str, ttl_seconds: float) -> None:
        """Store a value until its positive TTL expires."""
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("cache TTL must be positive")
        expires_at = self._clock() + ttl_seconds
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO research_cache(cache_key, value, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET value=excluded.value, "
                "expires_at=excluded.expires_at",
                (key, value, expires_at),
            )

    def reserve_request(self, source: str, min_interval_seconds: float) -> float:
        """Atomically reserve a request slot and return the seconds to wait first.

        Database errors deliberately propagate so callers fail closed without issuing a request.
        """
        if not source.strip() or not math.isfinite(min_interval_seconds) or min_interval_seconds < 0:
            raise ValueError("source and non-negative interval are required")
        now = self._clock()
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT next_allowed FROM research_rate WHERE source = ?", (source,)
            ).fetchone()
            next_allowed = float(row[0]) if row else now
            wait = max(0.0, next_allowed - now)
            reservation = max(now, next_allowed) + min_interval_seconds
            self._connection.execute(
                "INSERT INTO research_rate(source, next_allowed) VALUES (?, ?) "
                "ON CONFLICT(source) DO UPDATE SET next_allowed=excluded.next_allowed",
                (source, reservation),
            )
        return wait

    def close(self) -> None:
        """Close the backing SQLite connection."""
        with self._lock:
            self._connection.close()


def encode_cache_value(payload: dict[str, object]) -> str:
    """Serialize normalized cache data deterministically."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
