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
RECORDER_MAX_LIVENESS_AGE_SECONDS = 30
RECORDER_STATUS_PATH = Path(
    os.getenv("RECORDER_STATUS_PATH", "/workspace/data/recorder-status.json")
)
_RUNTIME_HEALTH_REASON: str | None = None
_RUNTIME_HEALTH_DETAIL: str | None = None


def set_runtime_health(reason: str | None, detail: str | None = None) -> None:
    """Set the short, secret-free runtime health reason exposed by /health."""
    global _RUNTIME_HEALTH_REASON, _RUNTIME_HEALTH_DETAIL
    if reason is not None and reason != "runtime_unavailable":
        raise ValueError("unsupported runtime health reason")
    if detail is not None and (not isinstance(detail, str) or len(detail) > 200):
        raise ValueError("runtime health detail must be a short string")
    _RUNTIME_HEALTH_REASON = reason
    _RUNTIME_HEALTH_DETAIL = detail if reason is not None else None


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
    expected_open = value.get("expected_open")
    stale_epics = value.get("stale_epics")
    ticks_received = value.get("ticks_received")
    last_tick_at = value.get("last_tick_at")
    if not isinstance(ticks_received, int) or ticks_received < 0:
        ticks_received = 0
    if not isinstance(reconnect_count, int) or reconnect_count < 0:
        reconnect_count = 0
    if not isinstance(stale_epics, list):
        stale_epics = []
    stale_epics = [epic for epic in stale_epics[:10] if isinstance(epic, str) and len(epic) <= 32]
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
        "market_status_source": (
            value.get("market_status_source")
            if isinstance(value.get("market_status_source"), str)
            and len(value["market_status_source"]) <= 120
            else "unknown"
        ),
        "expected_open": expected_open if isinstance(expected_open, bool) else None,
        "stale_epics": stale_epics,
        "ticks_received": ticks_received,
        "last_tick_at": last_tick_at if isinstance(last_tick_at, str) else None,
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
        "last_liveness_at": (
            value.get("last_liveness_at")
            if isinstance(value.get("last_liveness_at"), str)
            else None
        ),
    }


def _recorder_liveness_ok(
    recorder: dict[str, Any] | None,
    *,
    required: bool = False,
    now: datetime | None = None,
) -> bool:
    """Require fresh protocol and market-status heartbeats when recording is configured."""
    if recorder is None:
        return not required
    if not recorder["configured"]:
        return not required
    if recorder["status"] != "healthy" or recorder["ping_reply_ok"] is not True:
        return False
    if recorder["market_status"] == "unknown":
        return False
    if recorder.get("expected_open") is True:
        last_tick = recorder.get("last_tick_at")
        if not last_tick:
            return False
        try:
            tick_stamp = datetime.fromisoformat(last_tick.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return False
        if tick_stamp.tzinfo is None:
            return False
        tick_age = (now or datetime.now(UTC)) - tick_stamp.astimezone(UTC)
        tick_age_seconds = tick_age.total_seconds()
        if tick_age_seconds < 0 or tick_age_seconds > RECORDER_MAX_LIVENESS_AGE_SECONDS:
            return False
    last_liveness = recorder["last_liveness_at"]
    if not last_liveness:
        return False
    try:
        stamp = datetime.fromisoformat(last_liveness.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        return False
    age = (now or datetime.now(UTC)) - stamp.astimezone(UTC)
    return 0 <= age.total_seconds() <= RECORDER_MAX_LIVENESS_AGE_SECONDS


def health_payload() -> dict[str, Any]:
    """Return deployment metadata and dependency reachability without secrets."""
    settings = EnvironmentSettings()
    database_url, redis_url = _service_urls()
    database_ok = _probe_database(database_url)
    redis_ok = _probe_redis(redis_url)
    last_backup_at, backup_age, backup_fresh = _backup_freshness()
    backup_ok = settings.role != "all" or backup_fresh
    recorder = _data_recorder_status()
    recorder_credentials = all(
        value is not None and bool(value.get_secret_value())
        for value in (
            settings.capital_api_key,
            settings.capital_identifier,
            settings.capital_api_password,
        )
    )
    recorder_ok = settings.role != "all" or _recorder_liveness_ok(
        recorder, required=recorder_credentials
    )
    runtime_reason = _RUNTIME_HEALTH_REASON
    runtime_detail = _RUNTIME_HEALTH_DETAIL
    return {
        "status": (
            "healthy"
            if database_ok and redis_ok and backup_ok and recorder_ok and runtime_reason is None
            else "degraded"
        ),
        "runtime_reason": runtime_reason,
        "runtime_detail": runtime_detail,
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
