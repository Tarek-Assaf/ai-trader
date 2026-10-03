"""Liveness details for the local or RunPod deployment."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from trader.core.settings import EnvironmentSettings

BACKUP_DIR = Path("/workspace/backups")
BACKUP_MAX_AGE_SECONDS = 15 * 60
RECORDER_STATUS_PATH = Path(
    os.getenv("RECORDER_STATUS_PATH", "/workspace/data/recorder-status.json")
)


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
    backup_dir: Path | None = None,
    *,
    now: datetime | None = None,
) -> tuple[str | None, int | None, bool]:
    """Return the newest non-empty dump's UTC time, age, and freshness."""
    backup_dir = backup_dir or BACKUP_DIR
    current_time = now or datetime.now(UTC)
    newest_timestamp: float | None = None
    try:
        for backup in backup_dir.glob("trader-*.dump"):
            try:
                stat_result = backup.stat()
                if backup.is_file() and stat_result.st_size > 0:
                    modified_at = stat_result.st_mtime
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


def _data_recorder_status(path: Path | None = None) -> dict[str, Any] | None:
    """Expose a small allowlist of recorder liveness fields without leaking file contents."""
    try:
        value = json.loads((path or RECORDER_STATUS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    market_status = value.get("market_status")
    reconnect_count = value.get("reconnect_count")
    if not isinstance(reconnect_count, int) or reconnect_count < 0:
        reconnect_count = 0
    return {
        "configured": value.get("configured") is True,
        "status": (
            status
            if status in {"starting", "healthy", "degraded", "reconnecting", "not_configured"}
            else "unknown"
        ),
        "ping_reply_ok": (
            value.get("ping_reply_ok") if isinstance(value.get("ping_reply_ok"), bool) else None
        ),
        "market_status": (
            market_status
            if isinstance(market_status, str) and len(market_status) <= 40
            else "unknown"
        ),
        "reconnect_count": reconnect_count,
        "last_reconnect_reason": (
            value.get("last_reconnect_reason")
            if isinstance(value.get("last_reconnect_reason"), str)
            and len(value["last_reconnect_reason"]) <= 80
            else None
        ),
        "last_update_at": (
            value.get("last_update_at") if isinstance(value.get("last_update_at"), str) else None
        ),
        "last_closed_bar_at": (
            value.get("last_closed_bar_at")
            if isinstance(value.get("last_closed_bar_at"), str)
            else None
        ),
    }


def health_payload() -> dict[str, Any]:
    """Return deployment metadata and dependency reachability without secrets."""
    settings = EnvironmentSettings()
    database_url, redis_url = _service_urls()
    database_ok = _probe_database(database_url)
    redis_ok = _probe_redis(redis_url)
    last_backup_at, backup_age, backup_fresh = _backup_freshness()
    backup_ok = settings.role != "all" or backup_fresh
    recorder = _data_recorder_status()
    recorder_ok = (
        settings.role != "all"
        or recorder is None
        or not recorder["configured"]
        or recorder["status"] == "healthy"
    )
    return {
        "status": (
            "healthy" if database_ok and redis_ok and backup_ok and recorder_ok else "degraded"
        ),
        "role": settings.role,
        "version": os.getenv("GIT_SHA", "unknown"),
        "last_backup_at": last_backup_at,
        "last_backup_age_seconds": backup_age,
        "mode": settings.trading_mode,
        "broker_env": settings.broker_env,
        "live_enabled": settings.live_trading_enabled,
        "db": "reachable" if database_ok else "unreachable",
        "redis": "reachable" if redis_ok else "unreachable",
        "data_recorder": recorder,
        "time": datetime.now(UTC).isoformat(),
    }
