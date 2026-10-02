"""FastAPI application for health and operational status."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from trader.api.health import health_payload
from trader.core.config import load_all_configs
from trader.core.logging import configure_logging
from trader.core.settings import EnvironmentSettings


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
    load_all_configs(
        default_dir,
        volume_config_dir=volume_dir if volume_dir.is_dir() else None,
        baked_ceilings_path=baked_ceilings,
    )
    yield


app = FastAPI(title="AI Trader", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    """Expose role and dependency health without credentials or sensitive details."""
    return health_payload()
