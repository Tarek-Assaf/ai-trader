"""Liveness details for the local or RunPod deployment."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, text

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


def health_payload() -> dict[str, Any]:
    """Return deployment metadata and dependency reachability without secrets."""
    settings = EnvironmentSettings()
    database_url, redis_url = _service_urls()
    database_ok = _probe_database(database_url)
    redis_ok = _probe_redis(redis_url)
    return {
        "status": "healthy" if database_ok and redis_ok else "degraded",
        "role": settings.role,
        "version": os.getenv("GIT_SHA", "unknown"),
        "mode": settings.trading_mode,
        "broker_env": settings.broker_env,
        "live_enabled": settings.live_trading_enabled,
        "db": "reachable" if database_ok else "unreachable",
        "redis": "reachable" if redis_ok else "unreachable",
        "time": datetime.now(UTC).isoformat(),
    }
