#!/bin/sh
set -eu

# The supervisor include is generated from the active numbered accounts. Wait
# for every account API before allowing the recorder's Parquet backfill to run.
ports=$(/app/.venv/bin/python - <<'PY'
import re
from pathlib import Path

for line in Path("/workspace/supervisord.accounts.conf").read_text(encoding="utf-8").splitlines():
    match = re.search(r"command=.*\s(\d+)$", line)
    if match:
        print(8000 + int(match.group(1)))
PY
)

for port in $ports; do
    /app/scripts/wait_for_core_api.sh "http://127.0.0.1:${port}/health"
done
