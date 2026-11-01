"""Read-only commands for the extra accounts, answered by each account's own worker.

The main process owns Telegram; each extra account runs its own broker connection, database and
risk checks in a worker process on a loopback port.  To show an account's balance, positions, P&L
and risk, the main process asks that worker for the text of the same command it would have run
itself.  Only read-only commands are served, only to loopback callers, and never through the
public tunnel (the tunnel middleware already refuses everything but the alert route).
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable, Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request

LOGGER = logging.getLogger(__name__)

READ_ONLY_COMMANDS = frozenset({"/status", "/positions", "/pnl", "/risk", "/report", "/trades"})
# Commands that show every account when no account number is given.
ALL_ACCOUNT_COMMANDS = frozenset({"/positions", "/pnl", "/risk", "/report"})
ASK_TIMEOUT_SECONDS = 8.0
MAX_TEXT = 3500
_LOOPBACK = frozenset({"127.0.0.1", "::1"})

router = APIRouter()


def ask_worker(
    number: str, command: str, argument: str = "", *, timeout: float = ASK_TIMEOUT_SECONDS
) -> str | None:
    """The worker's answer to a read-only command, or None when it did not answer."""
    from trader.runtime.account_workers import worker_port

    if command not in READ_ONLY_COMMANDS:
        return None
    body = json.dumps({"command": command, "argument": argument[:200]}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{worker_port(number)}/internal/command",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.load(response)
    except Exception as exc:  # noqa: BLE001 - an account that is down must not fail the command
        LOGGER.warning("account %s did not answer %s (%s)", number, command, type(exc).__name__)
        return None
    text = payload.get("text") if isinstance(payload, dict) else None
    return text[:MAX_TEXT] if isinstance(text, str) and text else None


def unanswered(number: str) -> str:
    return f"Account {number} is not answering right now."


def split_account(argument: str, known: Mapping[str, str]) -> tuple[str | None, str]:
    """An account number at the start of the argument, if it names a known account."""
    first, _, rest = argument.strip().partition(" ")
    name = first.lower().removeprefix("acct-").removeprefix("account")
    if name.isdigit():
        name = name.zfill(2)
        if name in known or name == "01":
            return name, rest.strip()
    return None, argument.strip()


def render_for_accounts(
    command: str,
    argument: str,
    *,
    local: Callable[[str, str], str],
    workers: Callable[[], Mapping[str, str]],
    ask: Callable[..., str | None] = ask_worker,
) -> str:
    """Route one read-only command: one named account, or every account where that makes sense."""
    extra = workers()
    if not extra:
        return local(command, argument)
    number, rest = split_account(argument, extra)
    if number is not None and number != "01":
        return ask(number, command, rest) or unanswered(number)
    if number == "01":
        return local(command, rest)
    if command not in ALL_ACCOUNT_COMMANDS:
        return local(command, argument)
    blocks = [f"Account 01:\n{local(command, argument)}"]
    for extra_number, name in extra.items():
        answer = ask(extra_number, command, argument) or unanswered(extra_number)
        blocks.append(f"Account {extra_number} ({name}):\n{answer}")
    return "\n\n".join(blocks)


@router.post("/internal/command")
def internal_command(
    request: Request, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, str]:
    """Run a read-only command for this process's own account (loopback callers only)."""
    host = request.client.host if request.client else ""
    if host not in _LOOPBACK or request.headers.get("x-forwarded-for"):
        raise HTTPException(status_code=404, detail="not found")
    command = str(payload.get("command") or "")
    argument = str(payload.get("argument") or "")[:200]
    handlers = getattr(request.app.state, "local_command_handlers", None) or {}
    handler = handlers.get(command) if command in READ_ONLY_COMMANDS else None
    if not callable(handler):
        raise HTTPException(status_code=404, detail="not available")
    try:
        text = handler(f"{command} {argument}".strip())
    except Exception as exc:  # noqa: BLE001 - bounded failure, details stay in the log
        LOGGER.warning("internal command %s failed (%s)", command, type(exc).__name__)
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return {"text": str(text)[:MAX_TEXT]}
