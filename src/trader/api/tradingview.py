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
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from trader.execution.brokers.base import Direction

LOGGER = logging.getLogger(__name__)
router = APIRouter()

MIN_SECRET_LENGTH = 16
MAX_BODY_BYTES = 2000
# TradingView fires at the close of a 3-minute bar.  Later than this the price has moved
# on and the entry is no longer the one the indicator signalled.
MAX_ALERT_AGE_SECONDS = 170
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
async def tradingview_alert(request: Request) -> dict[str, Any]:
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
    if age > MAX_ALERT_AGE_SECONDS:
        LOGGER.warning("tradingview %s alert ignored: %ss after the bar closed", signal, age)
        return {"status": "stale", "signal": signal, "age_seconds": age}
    key = f"{symbol}:{signal}"
    with _lock:
        previous = _last_accepted.get(key)
        current = _monotonic()
        if previous is not None and current - previous < DUPLICATE_WINDOW_SECONDS:
            return {"status": "duplicate", "signal": signal}
        _last_accepted[key] = current
    outcome = await run_in_threadpool(runner.on_external_signal, Direction(signal))
    LOGGER.warning("tradingview %s alert -> %s %s", signal, outcome.action, outcome.reason)
    return {"status": outcome.action, "reason": outcome.reason, "signal": signal}
