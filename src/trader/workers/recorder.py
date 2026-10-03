"""Run the read-only Capital.com data recorder for the worker role."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from datetime import UTC, date, datetime, time as dt_time
from pathlib import Path
from typing import Any

import yaml

from trader.core.logging import configure_logging
from trader.core.settings import EnvironmentSettings
from trader.data.market_hours import MarketHours, resolve_market_open
from trader.data.recorder import CapitalDataRecorder, ParquetCandleStore
from trader.integrations.capital_com.client import CapitalComClient
from trader.integrations.capital_com.stream import CapitalComStreamClient

_LOG = logging.getLogger(__name__)
_MARKET_POLL_SECONDS = 60
_STATUS_POLL_SECONDS = 5
_DEFAULT_STALE_AFTER_SECONDS = 180.0


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
        holidays: list[date] = []
        early_closes: dict[date, dt_time] = {}
        try:
            holidays = [date.fromisoformat(value) for value in holidays_raw]
            early_closes = {
                date.fromisoformat(day): dt_time.fromisoformat(value) for day, value in early_raw.items()
            }
        except (TypeError, ValueError) as exc:
            raise ValueError("instrument holiday override date or time is invalid") from exc
        stale_after = session.get("stale_after_seconds", _DEFAULT_STALE_AFTER_SECONDS)
        if not isinstance(stale_after, (int, float)) or not 30 <= stale_after <= 3600:
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
    recorder = CapitalDataRecorder(ParquetCandleStore(lake_root), symbols_by_epic, status_path)
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
            stream = CapitalComStreamClient(
                rest,
                symbols_by_epic.keys(),
                recorder.on_update,
                on_reconnect=recorder.on_reconnect,
            )
            stream.seed_watermarks(recorder.watermarks())

            def run_stream() -> None:
                try:
                    stream.run_forever(stop)
                except Exception as exc:  # Do not include third-party exception text.
                    _LOG.error("Capital.com recorder stopped (%s)", type(exc).__name__)
                    stop.set()

            stream_thread = threading.Thread(
                target=run_stream, name="capital-data-recorder", daemon=True
            )
            stream_thread.start()
            market_status = "unknown"
            market_status_source = "unknown"
            expected_open: dict[str, bool | None] = {}
            stale_after_by_epic = {
                epic: config["stale_after_seconds"] for epic, config in session_settings.items()
            }
            next_market_poll = 0.0
            while not stop.is_set():
                heartbeat_path.touch()
                now = time.monotonic()
                if now >= next_market_poll:
                    statuses: list[str] = []
                    expected_open: dict[str, bool | None] = {}
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
                            is_open, source = resolve_market_open(
                                broker_status,
                                schedule,
                                datetime.now(UTC),
                                last_tick_at=recorder.last_tick_at(epic),
                                stale_after_seconds=config["stale_after_seconds"],
                            )
                            expected_open[epic] = is_open
                            statuses.append(
                                broker_status
                                if broker_status != "unknown"
                                else "TRADEABLE" if is_open is True
                                else "CLOSED" if is_open is False
                                else "unknown"
                            )
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
                )
                stop.wait(_STATUS_POLL_SECONDS)
            stop.set()
            stream_thread.join(timeout=35)
    finally:
        signal.signal(signal.SIGTERM, original_handler)


if __name__ == "__main__":
    run_data_recorder()
