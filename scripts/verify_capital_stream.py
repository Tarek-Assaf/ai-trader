"""Read-only, redacted Capital.com demo WebSocket verification for RunPod."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from datetime import UTC
from typing import Any

from trader.core.settings import EnvironmentSettings
from trader.integrations.capital_com import CapitalComClient, CapitalComStreamClient, OhlcUpdate


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--epic", default="US100", help="Capital.com epic to observe (default: US100)"
    )
    parser.add_argument(
        "--seconds", type=int, default=90, help="stream duration from 1 to 3600 seconds"
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    logging.disable(logging.CRITICAL)
    if args.seconds < 1 or args.seconds > 3600:
        print(json.dumps({"status": "error", "error": "seconds_out_of_range"}, sort_keys=True))
        return 2
    try:
        settings = EnvironmentSettings()
    except Exception as exc:
        print(json.dumps({"status": "error", "error": type(exc).__name__}, sort_keys=True))
        return 1
    if settings.broker_env != "demo" or settings.live_trading_enabled:
        print(json.dumps({"status": "refused", "reason": "demo_mode_required"}, sort_keys=True))
        return 2

    updates: dict[str, Any] = {
        "bid_updates": 0,
        "ask_updates": 0,
        "first_update_at": None,
        "last_update_at": None,
    }
    lock = threading.Lock()
    stop = threading.Event()
    failures: list[str] = []

    def on_update(update: OhlcUpdate) -> None:
        stamp = update.timestamp.astimezone(UTC).isoformat()
        with lock:
            field = f"{update.price_type}_updates"
            updates[field] += 1
            if updates["first_update_at"] is None:
                updates["first_update_at"] = stamp
            updates["last_update_at"] = stamp

    stream: CapitalComStreamClient | None = None
    worker: threading.Thread | None = None
    try:
        with CapitalComClient(settings) as rest:
            stream = CapitalComStreamClient(rest, [args.epic], on_update)

            def run_stream() -> None:
                try:
                    assert stream is not None
                    stream.run_forever(stop)
                except Exception as exc:
                    failures.append(type(exc).__name__)
                    stop.set()

            worker = threading.Thread(target=run_stream, name="capital-stream-check", daemon=True)
            worker.start()
            stop.wait(args.seconds)
            stop.set()
            worker.join(timeout=35)
    except Exception as exc:
        failures.append(type(exc).__name__)
        stop.set()

    result = {
        "status": "ok" if not failures else "error",
        "environment": "demo",
        "epic": args.epic,
        **updates,
        "ping_reply_ok": stream.ping_reply_ok if stream is not None else False,
        "reconnects": stream.reconnect_count if stream is not None else 0,
    }
    print(json.dumps(result, sort_keys=True))
    healthy = (
        not failures
        and worker is not None
        and not worker.is_alive()
        and result["ping_reply_ok"] is True
        and updates["bid_updates"] > 0
        and updates["ask_updates"] > 0
    )
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
