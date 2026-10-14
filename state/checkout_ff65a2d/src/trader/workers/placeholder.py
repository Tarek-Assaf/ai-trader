"""No-op worker used to validate the Phase 0 service role."""

from __future__ import annotations

import os
import time
from pathlib import Path


def run_placeholder() -> None:
    """Keep the worker process alive without running trading or network logic."""
    heartbeat = Path(os.getenv("WORKSPACE_ROOT", "/workspace")) / "worker.heartbeat"
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    while True:
        heartbeat.touch()
        time.sleep(5)


if __name__ == "__main__":
    run_placeholder()
