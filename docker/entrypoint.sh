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
ensure_trader_writable() {
    target=$1
    if gosu trader test -w "$target"; then
        return 0
    fi
    echo "trader user cannot write to $target" >&2
    return 1
}

set_trader_owner_or_verify() {
    target=$1
    if chown trader:trader "$target" 2>/dev/null; then
        ensure_trader_writable "$target"
        return $?
    fi
    if ensure_trader_writable "$target"; then
        printf 'Warning: chown failed for %s; trader can write to it, continuing\n' "$target" >&2
        return 0
    fi
    echo "Cannot chown $target and trader user cannot write to it" >&2
    return 1
}
if [ "$ROLE" = "all" ]; then
    case "$POSTGRES_DATA_DIR" in
        /*) ;;
        *) echo "POSTGRES_DATA_DIR must be an absolute path" >&2; exit 2 ;;
    esac
    mkdir -p "$POSTGRES_DATA_DIR"
fi
mkdir -p /workspace/redisdata /workspace/logs /workspace/backups /var/run/postgresql
if [ "$ROLE" = "all" ]; then
    rm -f /workspace/.postgres-restore-ready
    if ! set_trader_owner_or_verify "$POSTGRES_DATA_DIR"; then
        exit 1
    fi
    expected_uid=$(id -u trader)
    actual_uid=$(stat -c '%u' "$POSTGRES_DATA_DIR")
    if [ "$actual_uid" != "$expected_uid" ]; then
        echo "PostgreSQL data directory must be owned by trader (uid $expected_uid)" >&2
        exit 1
    fi
fi
for path in /workspace /workspace/redisdata /workspace/logs /workspace/backups /var/run/postgresql; do
    if ! set_trader_owner_or_verify "$path"; then
        exit 1
    fi
done
exec gosu trader supervisord -n -c "$CONFIG"
