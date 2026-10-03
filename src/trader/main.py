"""Application entry point; role-specific services are introduced in Phase 0."""

from __future__ import annotations

import os


def main() -> None:
    """Dispatch the configured application role without enabling live trading."""
    role = os.getenv("ROLE", "all")
    if role not in {"all", "core", "worker", "backtest"}:
        raise SystemExit(f"Unsupported ROLE={role!r}; expected all, core, worker, or backtest")
    if role == "backtest":
        print("Backtest runner is a Phase 0 placeholder.")
        return
    if role == "worker":
        from trader.workers.recorder import run_data_recorder

        run_data_recorder()
        return
    import uvicorn

    uvicorn.run("trader.api.app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()

