"""TradingView alert receiver: the owner's Tai Smart Filter decides when a signal exists.

TradingView sends the alert as a webhook.  This route only authenticates it and hands a
BUY or SELL to the Tai runner, which still applies the spread guard, the Risk Guardian
and the OrderManager.  The route has no way to choose size, stop or broker action.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from trader.execution.brokers.base import Direction

LOGGER = logging.getLogger(__name__)
router = APIRouter()

MIN_SECRET_LENGTH = 16
MAX_BODY_BYTES = 2000
# The alerts are set to "Once per bar close", so TradingView sends them within seconds of a
# 3-minute bar closing.  Anything arriving mid-bar is not a bar-close signal and the price has
# moved on; the last seconds of the previous bar count too (clock differences).
MAX_ALERT_AGE_SECONDS = 45
BAR_SECONDS = 180
EARLY_ALERT_SECONDS = 5
DUPLICATE_WINDOW_SECONDS = 120.0
# Other accounts run their own copy of the bot.  The owner lists their webhook addresses in
# TRADINGVIEW_FORWARD_URLS and this bot passes each authenticated BUY/SELL on unchanged, so one
# TradingView alert opens one trade per account.  A forwarded alert is never forwarded again.
FORWARD_URLS_ENV = "TRADINGVIEW_FORWARD_URLS"
FORWARD_FILE_NAME = "forward_urls"
FORWARD_HEADER = "X-Tai-Forwarded"
FORWARD_TIMEOUT_SECONDS = 4.0
FORWARD_ATTEMPTS = 2
FORWARD_RETRY_SECONDS = 2.0

_now: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731 - replaced in tests
_monotonic: Callable[[], float] = time.monotonic
_lock = threading.Lock()
_last_accepted: dict[str, float] = {}


def _seconds_into_bar(moment: datetime) -> int:
    return (moment.minute % 3) * 60 + moment.second


def _forward_file() -> Path:
    return Path(os.getenv("WORKSPACE_DIR", "/workspace")) / "state" / FORWARD_FILE_NAME


def _forward_targets() -> list[str]:
    """Webhook addresses to copy each alert to, from the environment and the forward file.

    The file (one address per line, ``#`` starts a comment) is read on every alert, so the owner
    can add an account without restarting this bot.  Anything that is not an http(s) address is
    skipped, and an address listed twice is used once.
    """
    entries = [part for part in os.getenv(FORWARD_URLS_ENV, "").split(",")]
    try:
        entries.extend(_forward_file().read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        pass
    except OSError:
        LOGGER.warning("the forward address file could not be read")
    targets: list[str] = []
    for part in entries:
        url = part.split("#", 1)[0].strip()
        if not url or url in targets:
            continue
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            LOGGER.warning("a forward address is not an http(s) address; skipped")
            continue
        targets.append(url)
    return targets


def _send_copy(url: str, body: bytes, timeout: float) -> int:
    """POST the alert once and return the HTTP status; raises on a network failure."""
    request = urllib.request.Request(  # noqa: S310 - scheme checked in _forward_targets
        url,
        data=body,
        headers={"Content-Type": "application/json", FORWARD_HEADER: "1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _forward_one(url: str, body: bytes) -> bool:
    host = urlsplit(url).hostname or "target"
    for attempt in range(1, FORWARD_ATTEMPTS + 1):
        try:
            status = _send_copy(url, body, FORWARD_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - one account must never block another
            LOGGER.warning(
                "tradingview alert copy to %s failed (attempt %d): %s",
                host,
                attempt,
                type(exc).__name__,
            )
        else:
            if status < 400:
                return True
            LOGGER.warning("tradingview alert copy to %s refused: HTTP %s", host, status)
            return False  # the other bot answered; sending it again would not change that
        if attempt < FORWARD_ATTEMPTS:
            time.sleep(FORWARD_RETRY_SECONDS)
    return False


def _forward(targets: list[str], body: bytes, signal: str) -> None:
    """Copy one authenticated alert to every other account's bot, in parallel."""
    try:
        with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
            results = list(pool.map(lambda url: _forward_one(url, body), targets))
        LOGGER.warning(
            "tradingview %s alert copied to %d of %d other account bot(s)",
            signal,
            sum(results),
            len(results),
        )
    except Exception:  # noqa: BLE001 - nothing is waiting on this task
        LOGGER.error("tradingview %s alert copy failed", signal, exc_info=True)


def _start_forward(targets: list[str], body: bytes, signal: str) -> None:
    """Run the copy on its own thread: a refusal below must not cancel it."""
    threading.Thread(
        target=_forward, args=(targets, body, signal), name="tradingview-forward", daemon=True
    ).start()


def _runner_for(runtime: Any, symbol: str) -> Any:
    """The runner for the alert's symbol; no symbol means the first (US100) runner."""
    if runtime is None:
        return None
    if symbol:
        from trader.experiments.tai_wiring import epic_aliases

        adapters = getattr(runtime, "adapters", None)
        if isinstance(adapters, dict):
            for epic in adapters:
                if symbol in epic_aliases(epic):
                    return getattr(adapters[epic], "runner", None)
            return None
    return getattr(runtime, "runner", None)


@router.post("/webhooks/tradingview")
async def tradingview_alert(request: Request, background: BackgroundTasks) -> dict[str, Any]:
    secret = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise HTTPException(status_code=404, detail="not found")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid alert") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid alert")
    supplied = str(payload.get("secret", ""))
    if not hmac.compare_digest(supplied.encode(), secret.encode()):
        raise HTTPException(status_code=403, detail="forbidden")
    signal = str(payload.get("signal", "")).strip().upper()
    if signal not in {"BUY", "SELL"}:
        return {"status": "ignored", "signal": signal[:20]}
    # Copy the alert to the other accounts before anything here can refuse it: this bot being
    # not ready, stale or paused says nothing about their bots, which judge the alert themselves.
    if not request.headers.get(FORWARD_HEADER):
        try:
            targets = _forward_targets()
            if targets:
                _start_forward(targets, raw, signal)
        except Exception:  # noqa: BLE001 - copying must never change this bot's own answer
            LOGGER.error("tradingview %s alert could not be copied", signal, exc_info=True)
    runtime = getattr(request.app.state, "tai_demo_runtime", None)
    symbol = str(payload.get("symbol", "")).strip().upper()
    runner = _runner_for(runtime, symbol)
    if runner is None or not callable(getattr(runner, "on_external_signal", None)):
        raise HTTPException(status_code=503, detail="trading runtime is not ready")
    age = _seconds_into_bar(_now().astimezone(UTC))
    if MAX_ALERT_AGE_SECONDS < age < BAR_SECONDS - EARLY_ALERT_SECONDS:
        LOGGER.warning("tradingview %s alert ignored: %ss after the bar closed", signal, age)
        return {"status": "stale", "signal": signal, "age_seconds": age}
    key = f"{symbol}:{signal}"
    with _lock:
        previous = _last_accepted.get(key)
        current = _monotonic()
        if previous is not None and current - previous < DUPLICATE_WINDOW_SECONDS:
            return {"status": "duplicate", "signal": signal}
        _last_accepted[key] = current
    # TradingView gives up after about 3 seconds, and placing the order takes longer.
    # Answer at once and trade after the response has been sent.
    background.add_task(_trade, runner, Direction(signal), signal, _close_price(payload))
    return {"status": "accepted", "signal": signal}


def _close_price(payload: dict[str, Any]) -> Decimal | None:
    """TradingView's bar close from the alert, when the message carries a usable one."""
    raw = payload.get("close")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() and value > 0 else None


def _trade(runner: Any, direction: Direction, signal: str, close: Decimal | None) -> None:
    try:
        outcome = (
            runner.on_external_signal(direction, reference_price=close)
            if close is not None
            else runner.on_external_signal(direction)
        )
        LOGGER.warning("tradingview %s alert -> %s %s", signal, outcome.action, outcome.reason)
    except Exception:  # noqa: BLE001 - nothing is waiting on this task
        LOGGER.error("tradingview %s alert failed", signal, exc_info=True)
