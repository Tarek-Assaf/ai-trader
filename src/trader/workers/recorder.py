"""Run the read-only Capital.com data recorder for the worker role."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from trader.core.logging import configure_logging
from trader.core.settings import EnvironmentSettings
from trader.data.recorder import CapitalDataRecorder, ParquetCandleStore
from trader.integrations.capital_com.client import CapitalComClient
from trader.integrations.capital_com.stream import CapitalComStreamClient

_LOG = logging.getLogger(__name__)
_MARKET_POLL_SECONDS = 60
_STATUS_POLL_SECONDS = 5


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
    status = payload.get("marketStatus")
    if status is None and isinstance(market, dict):
        status = market.get("marketStatus")
    return status if isinstance(status, str) and status else "unknown"


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
            next_market_poll = 0.0
            while not stop.is_set():
                heartbeat_path.touch()
                now = time.monotonic()
                if now >= next_market_poll:
                    statuses: list[str] = []
                    for epic in symbols_by_epic:
                        try:
                            statuses.append(_market_status(rest.market_details(epic)))
                        except Exception as exc:
                            _LOG.warning(
                                "Capital.com market status unavailable (%s)", type(exc).__name__
                            )
                            statuses.append("unknown")
                    market_status = ",".join(statuses) if statuses and all(statuses) else "unknown"
                    next_market_poll = now + _MARKET_POLL_SECONDS
                recorder.update_liveness(
                    ping_reply_ok=stream.ping_reply_ok,
                    market_status=market_status[:40] if market_status else "unknown",
                )
                stop.wait(_STATUS_POLL_SECONDS)
            stop.set()
            stream_thread.join(timeout=35)
    finally:
        signal.signal(signal.SIGTERM, original_handler)


if __name__ == "__main__":
    run_data_recorder()
