#!/bin/sh
set -eu

DATA_DIR=/workspace/pgdata
PG_BIN=$(find /usr/lib/postgresql -mindepth 3 -maxdepth 3 -type f -name initdb -print -quit)
if [ -z "$PG_BIN" ]; then
    echo "PostgreSQL initdb binary was not found" >&2
    exit 1
fi
PG_BIN=$(dirname "$PG_BIN")
mkdir -p "$DATA_DIR"
if [ ! -s "$DATA_DIR/PG_VERSION" ]; then
    # Trust is safe here because PostgreSQL listens on container loopback only and port 5432 is never published.
    "$PG_BIN/initdb" -D "$DATA_DIR" --username=trader --auth-local=trust --auth-host=trust --encoding=UTF8
fi

"$PG_BIN/postgres" -D "$DATA_DIR" -c listen_addresses=127.0.0.1 -c port=5432 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" || true; exit 0' TERM INT
READY=false
for _ in $(seq 1 60); do
    if "$PG_BIN/pg_isready" -h 127.0.0.1 -p 5432 -U trader >/dev/null 2>&1; then
        READY=true
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
wait "$SERVER_PID"
