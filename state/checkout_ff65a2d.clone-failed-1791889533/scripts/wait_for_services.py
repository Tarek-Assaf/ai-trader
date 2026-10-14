"""Wait for configured database and Redis endpoints before API migrations."""

from __future__ import annotations

import os
import time

from sqlalchemy import create_engine, text


def main() -> None:
    """Wait indefinitely for both stores; supervisor restarts this on failures."""
    database_url = os.environ.get("DATABASE_URL", "")
    redis_url = os.environ.get("REDIS_URL", "")
    deadline = time.monotonic() + 180
    last_error = "service unavailable"
    while time.monotonic() < deadline:
        try:
            if not database_url or not redis_url:
                raise RuntimeError("DATABASE_URL and REDIS_URL are required")
            engine = create_engine(database_url, connect_args={"connect_timeout": 2})
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
            from redis import Redis

            if Redis.from_url(redis_url, socket_connect_timeout=2).ping():
                return
        except Exception:  # noqa: BLE001 - do not print URLs, which may contain credentials
            last_error = "database or Redis is not ready"
            time.sleep(2)
    raise SystemExit(last_error)


if __name__ == "__main__":
    main()
