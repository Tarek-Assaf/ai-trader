"""FastAPI application for health and operational status."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from trader.api.health import health_payload
from trader.core.config import load_all_configs
from trader.core.logging import configure_logging
from trader.core.settings import EnvironmentSettings
from trader.data.instruments import InstrumentMapper
from trader.integrations.capital_com.client import CapitalComClient

_LOG = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    """Fail startup on invalid settings or config; register secret redaction."""
    settings = EnvironmentSettings()
    configure_logging(settings.sensitive_values())
    default_dir = Path("/app/config") if Path("/app/config").is_dir() else Path("config")
    baked_ceilings = Path("/app/baked/risk_ceilings.yaml")
    if not baked_ceilings.is_file():
        baked_ceilings = default_dir / "risk_ceilings.yaml"
    volume_dir = Path("/workspace/config")
    configs = load_all_configs(
        default_dir,
        volume_config_dir=volume_dir if volume_dir.is_dir() else None,
        baked_ceilings_path=baked_ceilings,
    )
    _app.state.instrument_mapper = None
    capital_client: CapitalComClient | None = None
    has_capital_credentials = all(
        value is not None and bool(value.get_secret_value())
        for value in (
            settings.capital_api_key,
            settings.capital_identifier,
            settings.capital_api_password,
        )
    )
    if settings.role in {"all", "core"} and has_capital_credentials:
        try:
            capital_client = CapitalComClient(settings)
            mapper = InstrumentMapper(configs["instruments"].model_dump(), capital_client)
            _app.state.instrument_mapper = mapper
            mapper.refresh_all()
        except Exception as exc:
            _app.state.instrument_mapper = None
            _LOG.warning("Capital.com instrument metadata unavailable (%s)", type(exc).__name__)
            if capital_client is not None:
                capital_client.close()
                capital_client = None
    try:
        yield
    finally:
        if capital_client is not None:
            capital_client.close()


app = FastAPI(title="AI Trader", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    """Expose role and dependency health without credentials or sensitive details."""
    return health_payload()
