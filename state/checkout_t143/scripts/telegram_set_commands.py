"""Owner-run Telegram command-menu setup; prints only a redacted success result."""

from __future__ import annotations

import json
import os
import sys

from trader.channels.telegram import TelegramBotTransport

COMMANDS = (
    ("status", "Show trading status"),
    ("positions", "Show open positions"),
    ("pnl", "Show profit and loss"),
    ("pause", "Pause new entries"),
    ("stop", "Flatten and pause"),
    ("risk", "Show risk limits"),
    ("report", "Show the latest report"),
    ("chart", "Render a chart"),
)


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print(json.dumps({"status": "missing", "required": "TELEGRAM_BOT_TOKEN"}))
        return 2
    try:
        TelegramBotTransport(token).set_commands(
            [{"command": command, "description": description} for command, description in COMMANDS]
        )
    except Exception:
        print(json.dumps({"status": "failed", "error": "telegram_request_failed"}))
        return 1
    print(json.dumps({"status": "ok"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
