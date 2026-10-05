"""Authenticated, LLM-free API for pause, kill and explicit owner resume."""

from __future__ import annotations

import hmac
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from trader.control.totp import verify_totp
from trader.core.settings import EnvironmentSettings
from trader.execution.control import ControlMode, ControlUnavailable

router = APIRouter()


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    totp_code: str = Field(pattern=r"^[0-9]{6}$")


@router.post("/control/{action}", status_code=202)
def control_action(
    action: Literal["pause", "kill", "resume"],
    payload: ControlRequest,
    request: Request,
    x_control_token: str = Header(default="", alias="X-Control-Token"),
) -> dict[str, str]:
    """Publish an owner-authenticated action for deterministic execution loops."""
    settings = EnvironmentSettings()
    expected_token = (
        settings.control_api_token.get_secret_value() if settings.control_api_token else ""
    )
    totp_secret = (
        settings.owner_totp_secret.get_secret_value() if settings.owner_totp_secret else ""
    )
    if (
        not expected_token
        or not totp_secret
        or not hmac.compare_digest(expected_token, x_control_token)
        or not verify_totp(totp_secret, payload.totp_code)
    ):
        raise HTTPException(status_code=401, detail="owner authorization failed")

    writer = getattr(request.app.state, "execution_control_writer", None)
    if writer is None:
        raise HTTPException(status_code=503, detail="execution control is unavailable")
    try:
        writer.set(ControlMode(action))
    except ControlUnavailable:
        raise HTTPException(status_code=503, detail="execution control is unavailable") from None
    return {"status": "accepted", "action": action}
