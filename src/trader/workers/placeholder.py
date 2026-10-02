"""No-op worker used to validate the Phase 0 service role."""

from __future__ import annotations

import time


def run_placeholder() -> None:
    """Keep the worker process alive without running trading or network logic."""
    while True:
        time.sleep(60)
