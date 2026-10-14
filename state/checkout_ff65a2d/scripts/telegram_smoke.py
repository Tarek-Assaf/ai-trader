"""Safe Telegram wiring smoke test for the RunPod image.

The default fixture mode performs no network calls. ``--live`` sends one status
message through the configured transport and still prints only environment
variable names and redacted results.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from trader.channels.telegram import TelegramBotTransport, TelegramChannel
from trader.execution.control import ControlMode


class _Writer:
    def __init__(self) -> None:
        self.mode = ControlMode.RUN

    def set(self, mode: ControlMode) -> None:
        self.mode = mode


class _FixtureTransport:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        del offset, timeout
        return []

    def send_message(self, chat_id: str, text: str, *, reply_markup: Any = None) -> None:
        del reply_markup
        self.messages.append((chat_id, text))

    def send_photo(self, chat_id: str, photo: bytes, *, caption: str = "") -> None:
        del photo, caption
        self.messages.append((chat_id, "photo"))

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        del callback_id, text


def _environment_report() -> dict[str, list[str]]:
    names = (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_OWNER_CHAT_IDS",
        "TELEGRAM_CHAT_ID",
        "LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OWNER_TOTP_SECRET",
        "CONTROL_API_TOKEN",
    )
    return {
        "set": [name for name in names if os.getenv(name)],
        "missing": [name for name in names if not os.getenv(name)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="send one status message via Telegram")
    args = parser.parse_args()
    owner_ids = tuple(
        value.strip()
        for value in (
            os.getenv("TELEGRAM_OWNER_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or ""
        ).split(",")
        if value.strip()
    )
    if not owner_ids:
        print(
            json.dumps({"result": "missing_owner_chat_ids", "environment": _environment_report()})
        )
        return 2
    if args.live:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            print(json.dumps({"result": "missing_token", "environment": _environment_report()}))
            return 2
        transport: Any = TelegramBotTransport(token)
        transport.send_message(owner_ids[0], "status OK")
        print(json.dumps({"result": "live_status_sent", "environment": _environment_report()}))
        return 0
    transport = _FixtureTransport()
    channel = TelegramChannel(transport, owner_ids, control_writer=_Writer())
    channel.handle_message(owner_ids[0], "status OK")
    non_owner = "fixture-non-owner"
    channel.handle_message(non_owner, "status OK")
    print(
        json.dumps(
            {
                "result": "fixture_pass",
                "owner_replies": len(transport.messages),
                "non_owner_replies": sum(chat == non_owner for chat, _text in transport.messages),
                "environment": _environment_report(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
