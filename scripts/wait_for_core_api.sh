#!/bin/sh
set -eu

# The all-in-one deployment has one small VM. Do not let the account workers or
# recorder compete with the core API's broker/configuration warm-up.
URL=${1:-http://127.0.0.1:8000/health}
exec /app/.venv/bin/python - "$URL" <<'PY'
import sys
import time
import urllib.request

url = sys.argv[1]
for _ in range(600):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status == 200:
                raise SystemExit(0)
    except Exception:
        pass
    time.sleep(1)

print("Core API did not become healthy before dependent startup", file=sys.stderr)
raise SystemExit(1)
PY
