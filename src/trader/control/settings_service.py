"""Owner-confirmed, fail-closed changes to risk and Strategy Lab settings."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

import yaml

from trader.control.totp import matching_totp_step
from trader.core.audit import AuditEvent, AuditWriter


class SettingsRejected(ValueError):
    """A requested setting change cannot be accepted safely."""


class SettingsNotifier(Protocol):
    def __call__(self, message: str) -> None: ...


class SettingsVersionStore(Protocol):
    def append(self, change: AppliedChange) -> None: ...


@dataclass(frozen=True, slots=True)
class SettingsChangeRequest:
    key: str
    new_value: Decimal | int | float
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PendingChange:
    change_id: str
    key: str
    old_value: Decimal | int | float
    new_value: Decimal | int | float
    direction: str
    requested_at: float
    effective_at: float
    reason: str


@dataclass(frozen=True, slots=True)
class AppliedChange:
    change_id: str
    key: str
    old_value: Decimal | int | float
    new_value: Decimal | int | float
    direction: str
    requested_at: float
    effective_at: float
    approved_by: str
    reason: str


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal | int | float | str):
        raise SettingsRejected("setting value must be numeric")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise SettingsRejected("setting value must be numeric") from None
    if not number.is_finite():
        raise SettingsRejected("setting value must be finite")
    return number


class SettingsService:
    """Apply typed setting requests with bounds, confirmation and TOTP gates."""

    _LOWER_IS_LOOSER = {
        "min_reward_risk",
        "min_trade_quality_score",
        "gate1_min_trades",
        "gate1_min_out_of_sample_trades",
        "gate1_min_walk_forward_windows",
        "gate2_min_weeks",
        "gate2_min_paper_trades",
        "gate3_min_weeks",
        "gate3_min_micro_trades",
    }
    _SETTABLE_KEYS = {
        "risk_per_trade_pct",
        "max_daily_loss_pct",
        "max_weekly_loss_pct",
        "max_drawdown_pct",
        "max_concurrent_live_strategies",
        "max_open_positions_total",
        "max_open_positions_per_instrument",
        "max_combined_risk_pct",
        "max_margin_usage_pct",
        "max_effective_leverage",
        "min_reward_risk",
        "min_trade_quality_score",
        "max_spread_atr_ratio",
        "max_orders_per_hour",
        "gate1_min_trades",
        "gate1_min_out_of_sample_trades",
        "gate1_min_walk_forward_windows",
        "gate2_min_weeks",
        "gate2_min_paper_trades",
        "gate3_min_weeks",
        "gate3_min_micro_trades",
    }

    def __init__(
        self,
        values: MutableMapping[str, Decimal | int | float],
        ceilings: Mapping[str, Any],
        *,
        totp_secret: str | None = None,
        audit: AuditWriter | None = None,
        notify: SettingsNotifier | None = None,
        version_store: SettingsVersionStore | None = None,
        config_path: Path | None = None,
        clock: Callable[[], float] = time.time,
        cooldown_seconds: float = 0.0,
    ) -> None:
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        self._values = values
        self._ceilings = ceilings
        self._totp_secret = totp_secret or ""
        self._audit = audit
        self._notify = notify
        self._version_store = version_store
        self._config_path = config_path
        self._clock = clock
        self._cooldown = cooldown_seconds
        self._pending: dict[str, PendingChange] = {}
        self._scheduled: dict[str, AppliedChange] = {}
        self._totp_failures = 0
        self._lockout_until = 0.0
        self._last_totp_step: int | None = None

    def request_change(
        self,
        request: SettingsChangeRequest | str,
        new_value: Decimal | int | float | None = None,
        *,
        halt_active: bool = False,
        reason: str = "",
    ) -> PendingChange:
        """Validate and stage one change; no value is written before confirm."""
        if isinstance(request, str):
            if new_value is None:
                raise SettingsRejected("new_value is required")
            request = SettingsChangeRequest(request, new_value, reason)
        if request.key not in self._SETTABLE_KEYS or request.key not in self._values:
            raise SettingsRejected("setting key is not changeable")
        old = self._values[request.key]
        old_number = _number(old)
        new_number = _number(request.new_value)
        if new_number == old_number:
            raise SettingsRejected("setting is unchanged")
        direction = self._direction(request.key, new_number, old_number)
        if direction == "loosening" and halt_active:
            raise SettingsRejected("loosening is refused while a halt is active")
        self._check_bounds(request.key, new_number)
        self._check_consistency(request.key, new_number)
        if direction == "loosening" and not self._totp_secret:
            raise SettingsRejected("loosening requires configured TOTP")
        now = self._clock()
        pending = PendingChange(
            change_id=secrets.token_urlsafe(12),
            key=request.key,
            old_value=old,
            new_value=request.new_value,
            direction=direction,
            requested_at=now,
            effective_at=now if direction == "tightening" else now + self._cooldown,
            reason=request.reason,
        )
        self._pending[pending.change_id] = pending
        self._record("settings_change_requested", pending)
        self._message(
            f"Change {pending.change_id} requested: {pending.key} {pending.old_value} -> "
            f"{pending.new_value} ({pending.direction}). Confirm before {self._expiry(pending)}."
        )
        return pending

    def confirm(self, change: PendingChange | str, *, totp_code: str = "") -> AppliedChange:
        """Confirm one staged change, applying tightening immediately."""
        change_id = change.change_id if isinstance(change, PendingChange) else change
        pending = self._pending.get(change_id)
        if pending is None:
            raise SettingsRejected("confirmation is expired or invalid")
        if self._clock() - pending.requested_at > 300:
            self._pending.pop(change_id, None)
            self._record("settings_change_rejected", pending, reason="confirmation_expired")
            self._message(f"Change {change_id} refused: confirmation expired.")
            raise SettingsRejected("confirmation is expired or invalid")
        if pending.direction == "loosening":
            try:
                self._verify_totp(totp_code)
            except SettingsRejected as exc:
                self._record("settings_change_rejected", pending, reason=str(exc))
                self._message(f"Change {change_id} refused: {exc}.")
                raise
        self._pending.pop(change_id, None)
        applied = AppliedChange(
            change_id=pending.change_id,
            key=pending.key,
            old_value=pending.old_value,
            new_value=pending.new_value,
            direction=pending.direction,
            requested_at=pending.requested_at,
            effective_at=pending.effective_at,
            approved_by="owner",
            reason=pending.reason,
        )
        if applied.effective_at > self._clock():
            self._scheduled[applied.change_id] = applied
            self._record("settings_change_scheduled", applied)
            self._message(
                f"Change {applied.change_id} scheduled for "
                f"{datetime.fromtimestamp(applied.effective_at, UTC).isoformat()}."
            )
            return applied
        self._apply(applied)
        return applied

    def apply_due(self) -> tuple[AppliedChange, ...]:
        """Apply confirmed loosening changes whose cooldown has elapsed."""
        now = self._clock()
        due = tuple(change for change in self._scheduled.values() if change.effective_at <= now)
        for change in due:
            self._scheduled.pop(change.change_id, None)
            self._apply(change)
        return due

    def _apply(self, applied: AppliedChange) -> None:
        self._values[applied.key] = applied.new_value
        self._persist()
        if self._version_store is not None:
            self._version_store.append(applied)
        self._record("settings_change_applied", applied)
        self._message(f"Change {applied.change_id} applied: {applied.key}={applied.new_value}.")

    def cancel(self, change: PendingChange | str) -> bool:
        """Cancel a pending change without touching the current settings."""
        change_id = change.change_id if isinstance(change, PendingChange) else change
        pending = self._pending.pop(change_id, None)
        if pending is None:
            if change_id in self._scheduled:
                scheduled = self._scheduled.pop(change_id)
                self._record("settings_change_cancelled", scheduled, reason="scheduled")
                self._message(f"Change {change_id} cancelled.")
                return True
            return False
        self._record("settings_change_cancelled", pending)
        self._message(f"Change {pending.change_id} cancelled.")
        return True

    def _direction(self, key: str, new: Decimal, old: Decimal) -> str:
        if key in self._LOWER_IS_LOOSER:
            return "loosening" if new < old else "tightening"
        return "tightening" if new < old else "loosening"

    def _check_bounds(self, key: str, value: Decimal) -> None:
        maximum = self._ceilings.get("max", {}).get(key)
        minimum = self._ceilings.get("min", {}).get(key)
        if minimum is None:
            minimum = self._ceilings.get("strategy_lab_floors", {}).get(key)
        if maximum is not None and value > _number(maximum):
            raise SettingsRejected(f"{key} exceeds baked ceiling {maximum}")
        if minimum is not None and value < _number(minimum):
            raise SettingsRejected(f"{key} is below baked floor {minimum}")

    def _check_consistency(self, key: str, value: Decimal) -> None:
        """Reject changes that would make related loss controls inconsistent."""
        if key == "max_weekly_loss_pct":
            daily = _number(self._values.get("max_daily_loss_pct", value))
            if value < daily:
                raise SettingsRejected("weekly loss limit cannot be below daily loss limit")
        elif key == "max_daily_loss_pct":
            weekly = _number(self._values.get("max_weekly_loss_pct", value))
            if value > weekly:
                raise SettingsRejected("daily loss limit cannot exceed weekly loss limit")

    def _verify_totp(self, code: str) -> None:
        now = self._clock()
        if now < self._lockout_until:
            raise SettingsRejected("TOTP loosening lockout is active")
        step = matching_totp_step(self._totp_secret, code, now=now)
        if step is None or (self._last_totp_step is not None and step <= self._last_totp_step):
            self._totp_failures += 1
            if self._totp_failures >= 3:
                self._lockout_until = now + 3600
                self._totp_failures = 0
            raise SettingsRejected("invalid or replayed TOTP")
        self._last_totp_step = step
        self._totp_failures = 0

    def _persist(self) -> None:
        if self._config_path is None:
            return
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        contents = yaml.safe_dump(dict(self._values), sort_keys=True)
        self._config_path.write_text(contents, encoding="utf-8")

    def _record(
        self,
        event_type: str,
        change: PendingChange | AppliedChange,
        *,
        reason: str = "",
    ) -> None:
        if self._audit is None:
            return
        payload = {
            "change_id": change.change_id,
            "key": change.key,
            "old_value": str(change.old_value),
            "new_value": str(change.new_value),
            "direction": change.direction,
            "reason": reason or change.reason,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self._audit.append(AuditEvent(event_type=event_type, payload=payload))

    def _message(self, text: str) -> None:
        if self._notify is not None:
            self._notify(text)

    @staticmethod
    def _expiry(change: PendingChange) -> str:
        return datetime.fromtimestamp(change.requested_at + 300, UTC).isoformat()
