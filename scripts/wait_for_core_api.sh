#!/bin/sh
set -eu

# The all-in-one deployment has one small VM. Do not let the account workers or
# recorder compete with the core API's broker/configuration warm-up.
URL=${1:-http://127.0.0.1:8000/health}
for _ in $(seq 1 600); do
    if WAIT_URL="$URL" /app/.venv/bin/python - <<'PY'
import os
import urllib.request

try:
    with urllib.request.urlopen(os.environ["WAIT_URL"], timeout=2) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
    then
        exit 0
    fi
    sleep 1
done

echo "Core API did not become healthy before dependent startup" >&2
exit 1
