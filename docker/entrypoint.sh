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

mkdir -p /workspace/pgdata /workspace/redisdata /workspace/logs
chown trader:trader /workspace /workspace/pgdata /workspace/redisdata /workspace/logs
exec gosu trader supervisord -n -c "$CONFIG"
