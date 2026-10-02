#!/bin/sh
set -eu

SLEEP_PID=
shutdown() {
    trap - TERM INT
    if [ -n "$SLEEP_PID" ]; then
        kill "$SLEEP_PID" 2>/dev/null || true
        wait "$SLEEP_PID" 2>/dev/null || true
    fi
    if ! /app/scripts/backup_postgres.sh; then
        echo "Final PostgreSQL backup failed" >&2
    fi
    exit 0
}
trap shutdown TERM INT

while :; do
    sleep 300 &
    SLEEP_PID=$!
    wait "$SLEEP_PID" || true
    SLEEP_PID=
    if ! /app/scripts/backup_postgres.sh; then
        echo "Scheduled PostgreSQL backup failed" >&2
    fi
done
