#!/bin/sh
set -eu

ROLE=${ROLE:-all}
case "$ROLE" in
    all) CONFIG=/app/docker/supervisord.all.conf ;;
    core) CONFIG=/app/docker/supervisord.core.conf ;;
    worker) CONFIG=/app/docker/supervisord.worker.conf ;;
    backtest) exec gosu trader python -m trader.backtest.runner ;;
    *) echo "Unsupported ROLE=$ROLE" >&2; exit 2 ;;
esac

POSTGRES_DATA_DIR=${POSTGRES_DATA_DIR:-/var/lib/ai-trader/pgdata}
if [ "$ROLE" = "all" ]; then
    case "$POSTGRES_DATA_DIR" in
        /*) ;;
        *) echo "POSTGRES_DATA_DIR must be an absolute path" >&2; exit 2 ;;
    esac
    mkdir -p "$POSTGRES_DATA_DIR"
fi
mkdir -p /workspace/redisdata /workspace/logs /workspace/backups /workspace/data/lake /var/run/postgresql
if [ "$ROLE" = "all" ]; then
    rm -f /workspace/.postgres-restore-ready
    chown trader:trader "$POSTGRES_DATA_DIR"
fi
chown trader:trader /workspace /workspace/redisdata /workspace/logs /workspace/backups /workspace/data /workspace/data/lake /var/run/postgresql
exec gosu trader supervisord -n -c "$CONFIG"

