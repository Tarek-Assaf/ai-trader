"""Status lines for the extra accounts, shown under the main account's /status."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from trader.reports.entry_state import entry_state_lines


def other_account_lines(
    names: Mapping[str, str],
    engine_for: Callable[[str], Any],
    mode_for: Callable[[str], Callable[[], Any]],
) -> list[str]:
    """One block per extra account: its control mode and whether entries are active or paused.

    Reading an account never fails the command: a worker that is down or has no database yet
    shows as unknown instead.
    """
    lines: list[str] = []
    for number, name in names.items():
        lines.append("")
        lines.append(f"Account {number} ({name}):")
        try:
            engine = engine_for(number)
        except Exception:  # noqa: BLE001 - a status line must never fail the command
            engine = None
        lines.extend(entry_state_lines(engine, mode_for(number)))
    return lines
