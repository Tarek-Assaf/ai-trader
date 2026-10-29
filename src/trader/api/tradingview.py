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
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

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

_now: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731 - replaced in tests
_monotonic: Callable[[], float] = time.monotonic
_lock = threading.Lock()
_last_accepted: dict[str, float] = {}


def _seconds_into_bar(moment: datetime) -> int:
    return (moment.minute % 3) * 60 + moment.second


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
