"""Liveness details for the local or RunPod deployment."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

BACKUP_DIR = Path("/workspace/backups")
BACKUP_MAX_AGE_SECONDS = 15 * 60

from trader.core.settings import EnvironmentSettings


def _service_urls() -> tuple[str, str]:
    """Return service endpoints without including credentials in any result."""
    role = os.getenv("ROLE", "all")
    database_url = os.getenv("DATABASE_URL")
    redis_url = os.getenv("REDIS_URL")
    if role == "all":
        database_url = database_url or "postgresql+psycopg://trader@127.0.0.1:5432/trader"
        redis_url = redis_url or "redis://127.0.0.1:6379/0"
    return database_url or "", redis_url or ""


def _probe_database(url: str) -> bool:
    """Check database reachability while suppressing connection details."""
    if not url:
        return False
    try:
        engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 2})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:  # noqa: BLE001 - health output must not expose connection details
        return False


def _probe_redis(url: str) -> bool:
    """Check Redis reachability while suppressing connection details."""
    if not url:
        return False
    try:
        from redis import Redis

        return bool(Redis.from_url(url, socket_connect_timeout=2).ping())
    except Exception:  # noqa: BLE001 - health output must not expose connection details
        return False


def _backup_freshness(
    backup_dir: Path = BACKUP_DIR,
    *,
    now: datetime | None = None,
) -> tuple[str | None, int | None, bool]:
    """Return the newest non-empty dump's UTC time, age, and freshness."""
    current_time = now or datetime.now(UTC)
    newest_timestamp: float | None = None
    try:
        for backup in backup_dir.glob("trader-*.dump"):
            try:
                if backup.is_file() and backup.stat().st_size > 0:
                    modified_at = backup.stat().st_mtime
                    newest_timestamp = max(newest_timestamp or modified_at, modified_at)
            except OSError:
                continue
    except OSError:
        pass

    if newest_timestamp is None:
        return None, None, False
    backup_time = datetime.fromtimestamp(newest_timestamp, UTC)
    age_seconds = max(0, int((current_time - backup_time).total_seconds()))
    return backup_time.isoformat(), age_seconds, age_seconds <= BACKUP_MAX_AGE_SECONDS


def health_payload() -> dict[str, Any]:
    """Return deployment metadata and dependency reachability without secrets."""
    settings = EnvironmentSettings()
    database_url, redis_url = _service_urls()
    database_ok = _probe_database(database_url)
    redis_ok = _probe_redis(redis_url)
    last_backup_at, backup_age, backup_fresh = _backup_freshness()
    backup_ok = settings.role != "all" or backup_fresh
    return {
        "status": "healthy" if database_ok and redis_ok and backup_ok else "degraded",
        "role": settings.role,
        "version": os.getenv("GIT_SHA", "unknown"),
        "last_backup_at": last_backup_at,
        "last_backup_age_seconds": backup_age,
        "mode": settings.trading_mode,
        "broker_env": settings.broker_env,
        "live_enabled": settings.live_trading_enabled,
        "db": "reachable" if database_ok else "unreachable",
        "redis": "reachable" if redis_ok else "unreachable",
        "time": datetime.now(UTC).isoformat(),
    }
