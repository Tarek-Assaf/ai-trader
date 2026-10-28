"""Run the read-only Capital.com data recorder for the worker role."""

from __future__ import annotations

import datetime
import logging
import os
import random
import signal
import threading
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any

import yaml

from trader.channels.telegram import TelegramBotTransport
from trader.core.logging import configure_logging
from trader.core.settings import EnvironmentSettings
from trader.data.market_hours import MarketHours, resolve_market_open
from trader.data.recorder import CapitalDataRecorder, ParquetCandleStore
from trader.integrations.capital_com.client import CapitalComClient
from trader.integrations.capital_com.stream import CapitalComStreamClient

_LOG = logging.getLogger(__name__)
_MARKET_POLL_SECONDS = 60
_STATUS_POLL_SECONDS = 5
_DEFAULT_STALE_AFTER_SECONDS = 60.0
_STREAM_RESTART_MIN_SECONDS = 3.0
_STREAM_RESTART_MAX_SECONDS = 300.0
_STREAM_HEALTHY_RESET_SECONDS = 300.0


def _next_stream_restart(
    backoff: float,
    healthy_seconds: float,
    *,
    jitter: Callable[[float, float], float] | None = None,
) -> tuple[float, float]:
    """Return a jittered restart wait and the next exponential backoff."""
    effective = (
        _STREAM_RESTART_MIN_SECONDS if healthy_seconds >= _STREAM_HEALTHY_RESET_SECONDS else backoff
    )
    effective = min(max(effective, _STREAM_RESTART_MIN_SECONDS), _STREAM_RESTART_MAX_SECONDS)
    jitter_fn = jitter or random.uniform
    wait = min(_STREAM_RESTART_MAX_SECONDS, effective + jitter_fn(0.0, effective * 0.25))
    return wait, min(effective * 2, _STREAM_RESTART_MAX_SECONDS)


def _config_path() -> Path:
    container = Path("/app/config/instruments.yaml")
    return container if container.is_file() else Path("config/instruments.yaml")


def _symbols_by_epic(path: Path) -> dict[str, str]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    instruments = payload.get("instruments") if isinstance(payload, dict) else None
    if not isinstance(instruments, dict):
        raise ValueError("instrument configuration is missing instruments")
    result: dict[str, str] = {}
    for symbol, instrument in instruments.items():
        if not isinstance(instrument, dict):
            raise ValueError("instrument configuration entry is invalid")
        epic = instrument.get("capital_epic")
        if not isinstance(epic, str) or not epic.strip():
            raise ValueError("instrument is missing its Capital.com epic")
        result[epic] = str(symbol)
    if not result:
        raise ValueError("instrument configuration contains no Capital.com epics")
    return result


def _market_status(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "unknown"
    market = payload.get("market")
    snapshot = payload.get("snapshot")
    status = payload.get("marketStatus")
    if status is None and isinstance(snapshot, dict):
        status = snapshot.get("marketStatus")
    if status is None and isinstance(market, dict):
        status = market.get("marketStatus")
    if not isinstance(status, str):
        return "unknown"
    normalized = status.strip().upper()
    return normalized if normalized in {"TRADEABLE", "CLOSED"} else "unknown"


def _combined_market_status(statuses: list[str]) -> str:
    """Fail closed when any required epic status is missing or malformed."""
    if not statuses or any(status == "unknown" for status in statuses):
        return "unknown"
    combined = ",".join(statuses)
    return combined if len(combined) <= 40 else "unknown"


def _resolved_market_status(broker_status: str, is_open: bool | None) -> str:
    if broker_status != "unknown":
        return broker_status
    if is_open is True:
        return "TRADEABLE"
    if is_open is False:
        return "CLOSED"
    return "unknown"


def _session_settings(path: Path) -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    instruments = payload.get("instruments") if isinstance(payload, dict) else None
    if not isinstance(instruments, dict):
        raise ValueError("instrument configuration is missing instruments")
    settings: dict[str, dict[str, Any]] = {}
    for instrument in instruments.values():
        if not isinstance(instrument, dict):
            raise ValueError("instrument configuration entry is invalid")
        epic = instrument.get("capital_epic")
        session = instrument.get("session", {})
        if not isinstance(epic, str) or not isinstance(session, dict):
            raise ValueError("instrument session configuration is invalid")
        holidays_raw = session.get("holiday_dates", [])
        early_raw = session.get("early_closes", {})
        if not isinstance(holidays_raw, list) or not isinstance(early_raw, dict):
            raise ValueError("instrument holiday overrides are invalid")
        holidays: list[datetime.date] = []
        early_closes: dict[datetime.date, datetime.time] = {}
        try:
            holidays = [datetime.date.fromisoformat(value) for value in holidays_raw]
            early_closes = {
                datetime.date.fromisoformat(day): datetime.time.fromisoformat(value)
                for day, value in early_raw.items()
            }
        except (TypeError, ValueError) as exc:
            raise ValueError("instrument holiday override date or time is invalid") from exc
        stale_after = session.get("stale_after_seconds", _DEFAULT_STALE_AFTER_SECONDS)
        if not isinstance(stale_after, int | float) or not 30 <= stale_after <= 3600:
            raise ValueError("stale_after_seconds must be between 30 and 3600")
        zone = session.get("timezone", "UTC")
        if not isinstance(zone, str):
            raise ValueError("instrument fallback timezone is invalid")
        settings[epic] = {
            "holidays": tuple(holidays),
            "early_closes": early_closes,
            "fallback_zone": zone,
            "stale_after_seconds": float(stale_after),
        }
    return settings


def _telegram_alert_sink(settings: EnvironmentSettings) -> Callable[[str], None] | None:
    """Build a best-effort Telegram sink without exposing credentials in logs."""
    token = (
        settings.telegram_bot_token.get_secret_value()
        if settings.telegram_bot_token is not None
        else ""
    )
    chat_ids = tuple(
        value.strip()
        for value in (settings.telegram_owner_chat_ids or "").split(",")
        if value.strip()
    )
    if not token or not chat_ids:
        return None
    transport = TelegramBotTransport(token)

    def send(message: str) -> None:
        for chat_id in chat_ids:
            try:
                transport.send_message(chat_id, message)
            except Exception:  # noqa: BLE001 - alerts must not stop recording
                _LOG.warning("Capital.com recorder Telegram alert failed")

    return send


def run_data_recorder() -> None:
    """Stream demo market data, reconcile gaps, and persist closed bars only."""
    settings = EnvironmentSettings()
    configure_logging(settings.sensitive_values())
    workspace = Path(os.getenv("WORKSPACE_ROOT", "/workspace"))
    lake_root = Path(os.getenv("DATA_LAKE_DIR", str(workspace / "data" / "lake")))
    status_path = Path(
        os.getenv("RECORDER_STATUS_PATH", str(workspace / "data" / "recorder-status.json"))
    )
    heartbeat_path = workspace / "worker.heartbeat"
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    symbols_by_epic = _symbols_by_epic(_config_path())
    recorder = CapitalDataRecorder(
        ParquetCandleStore(lake_root),
        symbols_by_epic,
        status_path,
        on_alert=_telegram_alert_sink(settings),
    )
    session_settings = _session_settings(_config_path())
    secrets = (
        settings.capital_api_key,
        settings.capital_identifier,
        settings.capital_api_password,
    )
    stop = threading.Event()
    original_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.set())
    if any(value is None or not value.get_secret_value() for value in secrets):
        recorder.set_unconfigured()
        _LOG.warning("Capital.com recorder is idle because credentials are not configured")
        while not stop.is_set():
            heartbeat_path.touch()
            stop.wait(_STATUS_POLL_SECONDS)
        signal.signal(signal.SIGTERM, original_handler)
        return

    try:
        with CapitalComClient(settings) as rest:
            expected_open: dict[str, bool | None] = {}
            stream = CapitalComStreamClient(
                rest,
                symbols_by_epic.keys(),
                recorder.enqueue_update,
                on_backfill_batch=recorder.on_backfill_batch,
                on_reconnect=recorder.on_reconnect,
                data_expected=lambda: any(value is True for value in expected_open.values()),
            )
            stream.seed_watermarks(recorder.watermarks())

            def run_stream() -> None:
                backoff = _STREAM_RESTART_MIN_SECONDS
                while not stop.is_set():
                    started = monotonic()
                    try:
                        stream.run_forever(stop)
                        return
                    except Exception as exc:
                        healthy_seconds = monotonic() - started
                        wait, backoff = _next_stream_restart(backoff, healthy_seconds)
                        if isinstance(exc, ValueError):
                            _LOG.error(
                                "Capital.com recorder stopped (%s): %s",
                                type(exc).__name__,
                                str(exc)[:160],
                            )
                        else:
                            _LOG.error("Capital.com recorder stopped (%s)", type(exc).__name__)
                        if stop.wait(wait):
                            return

            stream_thread = threading.Thread(
                target=run_stream, name="capital-data-recorder", daemon=True
            )
            stream_thread.start()
            market_status = "unknown"
            market_status_source = "unknown"
            market_hours_by_epic: dict[str, MarketHours | None] = {}
            stale_after_by_epic = {
                # Runtime alert policy is capped at one minute while preserving
                # the owner-verified value in parsed configuration.
                epic: min(config["stale_after_seconds"], 60.0)
                for epic, config in session_settings.items()
            }
            next_market_poll = 0.0
            while not stop.is_set():
                heartbeat_path.touch()
                recorder.drain_updates()
                now = monotonic()
                if now >= next_market_poll:
                    statuses: list[str] = []
                    expected_open = {}
                    market_hours_by_epic = {}
                    sources: list[str] = []
                    for epic in symbols_by_epic:
                        try:
                            details = rest.market_details(epic)
                            broker_status = _market_status(details)
                            config = session_settings[epic]
                            try:
                                schedule = MarketHours.from_market_details(
                                    details,
                                    holidays=config["holidays"],
                                    early_closes=config["early_closes"],
                                    fallback_zone=config["fallback_zone"],
                                )
                            except ValueError as exc:
                                schedule = None
                                _LOG.warning(
                                    "Capital.com opening hours unavailable (%s)",
                                    type(exc).__name__,
                                )
                            market_hours_by_epic[epic] = schedule
                            is_open, source = resolve_market_open(
                                broker_status,
                                schedule,
                                datetime.datetime.now(datetime.UTC),
                            )
                            expected_open[epic] = is_open
                            statuses.append(_resolved_market_status(broker_status, is_open))
                            sources.append(source)
                        except Exception as exc:
                            _LOG.warning(
                                "Capital.com market status unavailable (%s)", type(exc).__name__
                            )
                            statuses.append("unknown")
                            expected_open[epic] = None
                            sources.append("unknown")
                    market_status = _combined_market_status(statuses)
                    market_status_source = ",".join(sources)
                    next_market_poll = now + _MARKET_POLL_SECONDS
                recorder.update_liveness(
                    ping_reply_ok=stream.ping_reply_ok,
                    market_status=market_status[:40] if market_status else "unknown",
                    expected_open_by_epic=expected_open,
                    market_status_source=market_status_source,
                    stale_after_seconds_by_epic=stale_after_by_epic,
                    market_hours_by_epic=market_hours_by_epic,
                )
                stop.wait(_STATUS_POLL_SECONDS)
            stop.set()
            stream_thread.join(timeout=35)
            while recorder.drain_updates(max_updates=4096):
                pass
    finally:
        signal.signal(signal.SIGTERM, original_handler)


if __name__ == "__main__":
    run_data_recorder()
