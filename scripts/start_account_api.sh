#!/bin/sh
# Start one extra account's API process inside the container: ./start_account_api.sh 02
# The account's credentials are read from the container environment and kept in this process
# only; nothing is written to disk and nothing is printed.
set -eu

NUMBER=${1:-}
case "$NUMBER" in
    ''|*[!0-9]*) echo "usage: start_account_api.sh <account number>" >&2; exit 2 ;;
esac

for _ in $(seq 1 600); do
    [ -f /workspace/.postgres-restore-ready ] && break
    sleep 1
done
if [ ! -f /workspace/.postgres-restore-ready ]; then
    echo "PostgreSQL restore did not finish before account $NUMBER startup" >&2
    exit 1
fi

# Both lookups read the container's own settings, so they run BEFORE this process takes on the
# worker's login: afterwards the unnumbered login equals account NN's, and the lookup would see two
# accounts with one login and drop account NN.
python -m trader.runtime.account_workers database "$NUMBER" || exit 4
# A failed lookup must stop this worker, not run it with the main account's settings.
EXPORTS=$(python -m trader.runtime.account_workers env "$NUMBER") || exit 3
eval "$EXPORTS"
unset EXPORTS

mkdir -p "$WORKSPACE_DIR/state"
python /app/scripts/wait_for_services.py
alembic upgrade head

exec uvicorn trader.api.app:app --host 127.0.0.1 --port "$ACCOUNT_PORT"
