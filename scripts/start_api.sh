#!/bin/sh
set -eu

ROLE=${ROLE:-all}
if [ "$ROLE" = "all" ]; then
    export DATABASE_URL=${DATABASE_URL:-postgresql+psycopg://trader@127.0.0.1:5432/trader}
    export REDIS_URL=${REDIS_URL:-redis://127.0.0.1:6379/0}
fi

if [ "$ROLE" = "all" ] || [ "$ROLE" = "core" ]; then
    python /app/scripts/wait_for_services.py
    alembic upgrade head
fi

exec uvicorn trader.api.app:app --host 0.0.0.0 --port 8000
