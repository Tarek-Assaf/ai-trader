#!/bin/sh
set -eu

# The all-in-one deployment has one small VM. Do not let the account workers or
# recorder compete with the core API's broker/configuration warm-up.
for _ in $(seq 1 600); do
    if /app/.venv/bin/python - <<'PY'
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
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
