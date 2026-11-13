"""Authenticated, LLM-free API for pause, kill and explicit owner resume."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from trader.core.settings import EnvironmentSettings
from trader.execution.control import ControlMode, ControlUnavailable

router = APIRouter()


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    totp_code: str = Field(pattern=r"^[0-9]{6}$")


class ResolveIntentRequest(ControlRequest):
    """Owner-only evidence used to resolve one ambiguous broker intent."""

    cycle_id: str = Field(min_length=1, max_length=200)
    outcome: Literal["matched", "rejected"]
    deal_id: str | None = Field(default=None, max_length=128)
    reason: str = Field(min_length=1, max_length=500)


def _authorized_writer(request: Request, x_control_token: str) -> tuple[Any, str, str]:
    """Return the authenticated control writer or raise a bounded HTTP error."""
    settings = EnvironmentSettings()
    expected_token = (
        settings.control_api_token.get_secret_value() if settings.control_api_token else ""
    )
    totp_secret = (
        settings.owner_totp_secret.get_secret_value() if settings.owner_totp_secret else ""
    )
    if not expected_token or not totp_secret:
        raise HTTPException(status_code=401, detail="owner authorization failed")
    writer = getattr(request.app.state, "execution_control_all_writer", None) or getattr(
        request.app.state, "execution_control_writer", None
    )
    if writer is None:
        raise HTTPException(status_code=503, detail="execution control is unavailable")
    return writer, expected_token, totp_secret


@router.post("/control/{action}", status_code=202)
def control_action(
    action: Literal["pause", "kill", "resume"],
    payload: ControlRequest,
    request: Request,
    x_control_token: str = Header(default="", alias="X-Control-Token"),
) -> dict[str, str]:
    """Publish an owner-authenticated action for deterministic execution loops."""
    writer, expected_token, totp_secret = _authorized_writer(request, x_control_token)
    try:
        if not writer.authorize(expected_token, x_control_token, totp_secret, payload.totp_code):
            raise HTTPException(status_code=401, detail="owner authorization failed")
        writer.set(ControlMode(action))
    except ControlUnavailable:
        raise HTTPException(status_code=503, detail="execution control is unavailable") from None
    return {"status": "accepted", "action": action}


@router.post("/control/resolve-intent", status_code=202)
def resolve_intent(
    payload: ResolveIntentRequest,
    request: Request,
    x_control_token: str = Header(default="", alias="X-Control-Token"),
) -> dict[str, str]:
    """Resolve one ambiguous intent after authenticated broker evidence review.

    This endpoint is deliberately separate from pause/resume: it never changes a
    risk limit or clears another account's daily-loss guard.  The manager performs
    the final broker position/activity checks before journaling the outcome.
    """
    writer, expected_token, totp_secret = _authorized_writer(request, x_control_token)
    try:
        if not writer.authorize(expected_token, x_control_token, totp_secret, payload.totp_code):
            raise HTTPException(status_code=401, detail="owner authorization failed")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="execution control is unavailable") from None

    services = getattr(request.app.state, "runtime_services", None) or {}
    manager = services.get("order_manager") if isinstance(services, dict) else None
    resolver = getattr(manager, "resolve_intent", None)
    if not callable(resolver):
        raise HTTPException(status_code=503, detail="execution manager is unavailable")
    try:
        resolver(
            payload.cycle_id,
            payload.outcome,
            deal_id=payload.deal_id,
            reason=payload.reason,
        )
    except Exception:
        raise HTTPException(status_code=409, detail="intent resolution rejected") from None
    return {"status": "accepted", "cycle_id": payload.cycle_id, "outcome": payload.outcome}
