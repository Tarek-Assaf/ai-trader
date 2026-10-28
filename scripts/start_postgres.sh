#!/bin/sh
set -eu

DATA_DIR=${POSTGRES_DATA_DIR:-/var/lib/ai-trader/pgdata}
case "$DATA_DIR" in
    /*) ;;
    *) echo "POSTGRES_DATA_DIR must be an absolute path" >&2; exit 2 ;;
esac
BACKUP_DIR=/workspace/backups
RESTORE_READY=/workspace/.postgres-restore-ready
rm -f "$RESTORE_READY"
mkdir -p "$DATA_DIR" "$BACKUP_DIR"

PG_BIN=$(find /usr/lib/postgresql -mindepth 3 -maxdepth 3 -type f -name initdb -print -quit)
if [ -z "$PG_BIN" ]; then
    echo "PostgreSQL initdb binary was not found" >&2
    exit 1
fi
PG_BIN=$(dirname "$PG_BIN")
NEW_CLUSTER=false
if [ ! -s "$DATA_DIR/PG_VERSION" ]; then
    # Trust is safe because PostgreSQL listens on loopback only and port 5432 is never published.
    "$PG_BIN/initdb" -D "$DATA_DIR" --username=trader --auth-local=trust --auth-host=trust --encoding=UTF8
    NEW_CLUSTER=true
fi

"$PG_BIN/postgres" -D "$DATA_DIR" -c listen_addresses=127.0.0.1 -c port=5432 &
SERVER_PID=$!
shutdown() {
    trap - TERM INT
    if [ -n "${SERVER_PID:-}" ]; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    exit 0
}
trap shutdown TERM INT

READY=false
for _ in $(seq 1 60); do
    if "$PG_BIN/pg_isready" -h 127.0.0.1 -p 5432 -U trader >/dev/null 2>&1; then
        READY=true
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
    fi
    sleep 1
done
if [ "$READY" != true ]; then
    echo "PostgreSQL did not become ready" >&2
    exit 1
fi
if ! "$PG_BIN/psql" -h 127.0.0.1 -U trader -lqt | cut -d '|' -f 1 | grep -qw trader; then
    "$PG_BIN/createdb" -h 127.0.0.1 -U trader trader
fi

if [ "$NEW_CLUSTER" = true ]; then
    BACKUP_COUNT=0
    RESTORED=false
    for backup in $(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'trader-*.dump' -print | LC_ALL=C sort -r); do
        BACKUP_COUNT=$((BACKUP_COUNT + 1))
        if ! "$PG_BIN/pg_restore" --list "$backup" >/dev/null 2>&1; then
            echo "Skipping an unreadable PostgreSQL backup"
            continue
        fi
        if "$PG_BIN/pg_restore" --host=127.0.0.1 --username=trader --exit-on-error --single-transaction --no-owner --no-privileges --dbname=trader "$backup"; then
            if AUDIT_OK=$("$PG_BIN/psql" -h 127.0.0.1 -U trader -d trader -Atqc "SELECT verify_audit_log()"); then
                if [ "$AUDIT_OK" = "t" ]; then
                    echo "Restored PostgreSQL from a verified backup"
                    RESTORED=true
                    break
                fi
            fi
        fi
        echo "PostgreSQL backup could not be restored and verified; trying the previous backup"
        "$PG_BIN/dropdb" -h 127.0.0.1 -U trader trader
        "$PG_BIN/createdb" -h 127.0.0.1 -U trader trader
    done
    if [ "$BACKUP_COUNT" -gt 0 ] && [ "$RESTORED" != true ]; then
        echo "No valid PostgreSQL backup could be restored; refusing to start with an empty database" >&2
        exit 1
    fi
fi

"$PG_BIN/pg_ctl" -D "$DATA_DIR" -m fast -w stop
wait "$SERVER_PID" 2>/dev/null || true
SERVER_PID=
touch "$RESTORE_READY"
trap - TERM INT
exec "$PG_BIN/postgres" -D "$DATA_DIR" -c listen_addresses=127.0.0.1 -c port=5432
