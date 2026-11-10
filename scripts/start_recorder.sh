#!/bin/sh
set -eu

# Recorder startup reads the persisted lake and may perform a broker backfill.
# Let the core API finish its one-time warm-up first so the 2-vCPU guest remains
# responsive and health/SSH are not starved during container boot.
/app/scripts/wait_for_core_api.sh
exec /app/.venv/bin/python -m trader.workers.recorder
