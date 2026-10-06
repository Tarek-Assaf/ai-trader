"""Owner-confirmed, fail-closed changes to risk and Strategy Lab settings."""

from __future__ import annotations

import secrets
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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


def _stored_number(value: Decimal | int | float) -> int | float:
    number = _number(value)
    if number == number.to_integral_value():
        return int(number)
    return float(number)


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
    _COUNT_KEYS = {
        "max_concurrent_live_strategies",
        "max_open_positions_total",
        "max_open_positions_per_instrument",
        "max_orders_per_hour",
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
    _KEY_PATHS = {
        "gate1_min_trades": ("strategy_lab.yaml", ("gate1_backtest_to_paper", "min_trades")),
        "gate1_min_out_of_sample_trades": (
            "strategy_lab.yaml",
            ("gate1_backtest_to_paper", "min_out_of_sample_trades"),
        ),
        "gate1_min_walk_forward_windows": (
            "strategy_lab.yaml",
            ("gate1_backtest_to_paper", "min_walk_forward_windows"),
        ),
        "gate2_min_weeks": ("strategy_lab.yaml", ("gate2_paper_to_micro_live", "min_weeks")),
        "gate2_min_paper_trades": (
            "strategy_lab.yaml",
            ("gate2_paper_to_micro_live", "min_trades"),
        ),
        "gate3_min_weeks": ("strategy_lab.yaml", ("gate3_micro_to_live", "min_weeks")),
        "gate3_min_micro_trades": ("strategy_lab.yaml", ("gate3_micro_to_live", "min_trades")),
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
        config_dir: Path | None = None,
        clock: Callable[[], float] = time.time,
        cooldown_seconds: float | None = None,
        halt_active: Callable[[], bool],
    ) -> None:
        if cooldown_seconds is not None and cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        self._values, self._ceilings = values, ceilings
        self._totp_secret = totp_secret or ""
        self._audit, self._notify, self._version_store = audit, notify, version_store
        self._config_path, self._config_dir = config_path, config_dir
        self._clock, self._cooldown = clock, cooldown_seconds
        self._halt_active = halt_active
        self._pending: dict[str, PendingChange] = {}
        self._scheduled: dict[str, AppliedChange] = {}
        self._applied: dict[str, AppliedChange] = {}
        self._totp_failures, self._lockout_until = 0, 0.0
        self._last_totp_step: int | None = None
        self._startup_refused = False
        self._lock = threading.RLock()
        self._load_pending()

    @property
    def trading_allowed(self) -> bool:
        return not self._startup_refused

    def request_change(
        self,
        request: SettingsChangeRequest | str,
        new_value: Decimal | int | float | None = None,
        *,
        halt_active: bool | None = None,
        reason: str = "",
    ) -> PendingChange:
        with self._lock:
            try:
                return self._request_change(
                    request, new_value, halt_active=halt_active, reason=reason
                )
            except SettingsRejected as exc:
                self._refuse(
                    request.key if isinstance(request, SettingsChangeRequest) else request, str(exc)
                )
                raise

    def _request_change(
        self,
        request: SettingsChangeRequest | str,
        new_value: Decimal | int | float | None,
        *,
        halt_active: bool | None,
        reason: str,
    ) -> PendingChange:
        if isinstance(request, str):
            if new_value is None:
                raise SettingsRejected("new_value is required")
            request = SettingsChangeRequest(request, new_value, reason)
        if request.key not in self._SETTABLE_KEYS or request.key not in self._values:
            raise SettingsRejected("setting key is not changeable")
        self._prune_expired()
        if any(change.key == request.key for change in self._pending.values()) or any(
            change.key == request.key for change in self._scheduled.values()
        ):
            raise SettingsRejected("a change for this setting is already pending")
        old = self._values[request.key]
        old_number, new_number = _number(old), self._request_number(request.new_value, request.key)
        if new_number == old_number:
            raise SettingsRejected("setting is unchanged")
        direction = self._direction(request.key, new_number, old_number)
        if direction == "loosening" and self._is_halted(halt_active):
            raise SettingsRejected("loosening is refused while a halt is active")
        self._check_bounds(request.key, new_number)
        self._check_consistency(request.key, new_number)
        if direction == "loosening" and not self._totp_secret:
            raise SettingsRejected("loosening requires configured TOTP")
        now = self._clock()
        pending = PendingChange(
            secrets.token_urlsafe(12),
            request.key,
            old,
            request.new_value,
            direction,
            now,
            now if direction == "tightening" else 0.0,
            request.reason,
        )
        self._pending[pending.change_id] = pending
        self._record("settings_change_requested", pending)
        self._message(
            f"Change {pending.change_id} requested: {pending.key} {pending.old_value} -> "
            f"{pending.new_value} ({pending.direction}). Confirm before {self._expiry(pending)}."
        )
        return pending

    def confirm(self, change: PendingChange | str, *, totp_code: str = "") -> AppliedChange:
        with self._lock:
            change_id = change.change_id if isinstance(change, PendingChange) else change
            try:
                return self._confirm(change_id, totp_code)
            except SettingsRejected as exc:
                self._refuse(change_id, str(exc))
                raise

    def _confirm(self, change_id: str, totp_code: str) -> AppliedChange:
        pending = self._pending.get(change_id)
        if pending is None:
            raise SettingsRejected("confirmation is expired or invalid")
        now = self._clock()
        if now - pending.requested_at > 300:
            self._pending.pop(change_id, None)
            self._record("settings_change_rejected", pending, reason="confirmation_expired")
            raise SettingsRejected("confirmation is expired or invalid")
        current, requested = (
            _number(self._values[pending.key]),
            self._request_number(pending.new_value, pending.key),
        )
        direction = self._direction(pending.key, requested, current)
        if direction == "loosening" and self._is_halted(None):
            raise SettingsRejected("loosening is refused while a halt is active")
        self._check_bounds(pending.key, requested)
        self._check_consistency(pending.key, requested)
        if direction == "loosening":
            self._verify_totp(totp_code)
            effective_at = self._effective_at(now)
        else:
            effective_at = now
        self._pending.pop(change_id, None)
        applied = AppliedChange(
            pending.change_id,
            pending.key,
            self._values[pending.key],
            _stored_number(pending.new_value),
            direction,
            pending.requested_at,
            effective_at,
            "owner",
            pending.reason,
        )
        if effective_at > now:
            self._scheduled[applied.change_id] = applied
            self._save_pending()
            self._record("settings_change_scheduled", applied)
            effective_text = datetime.fromtimestamp(effective_at, UTC).isoformat()
            self._message(f"Change {applied.change_id} scheduled for {effective_text}.")
            return applied
        self._apply(applied)
        return applied

    def apply_due(self) -> tuple[AppliedChange, ...]:
        with self._lock:
            now = self._clock()
            due = tuple(change for change in self._scheduled.values() if change.effective_at <= now)
            applied: list[AppliedChange] = []
            for change in due:
                self._scheduled.pop(change.change_id, None)
                current = _number(self._values[change.key])
                try:
                    if current != _number(change.old_value):
                        raise SettingsRejected("current value changed since confirmation")
                    self._check_bounds(change.key, _number(change.new_value))
                    self._check_consistency(change.key, _number(change.new_value))
                    if self._is_halted(None):
                        raise SettingsRejected("halt_active")
                except SettingsRejected as exc:
                    self._record("settings_change_rejected", change, reason=str(exc))
                    self._message(f"Change {change.change_id} refused: {exc}.")
                    continue
                self._apply(change)
                applied.append(change)
            self._save_pending()
            return tuple(applied)

    def cancel(self, change: PendingChange | str) -> bool:
        with self._lock:
            change_id = change.change_id if isinstance(change, PendingChange) else change
            pending = self._pending.pop(change_id, None)
            if pending is not None:
                self._record("settings_change_cancelled", pending)
                self._message(f"Change {change_id} cancelled.")
                self._save_pending()
                return True
            scheduled = self._scheduled.pop(change_id, None)
            if scheduled is not None:
                self._record("settings_change_cancelled", scheduled, reason="scheduled")
                self._message(f"Change {change_id} cancelled.")
                self._save_pending()
                return True
            self._refuse(change_id, "change is expired or invalid")
            return False

    def revert(self, change_id: str) -> PendingChange:
        with self._lock:
            applied = self._applied.get(change_id)
            if applied is None:
                self._refuse(change_id, "change is not available for revert")
                raise SettingsRejected("change is not available for revert")
            return self.request_change(
                SettingsChangeRequest(applied.key, applied.old_value, reason=f"revert {change_id}")
            )

    def reconcile_startup(self, version_values: Mapping[str, Any]) -> bool:
        with self._lock:
            mismatch = False
            for key, persisted in version_values.items():
                if key not in self._values:
                    continue
                current, previous = _number(self._values[key]), _number(persisted)
                if current == previous:
                    continue
                mismatch = True
                stricter = (
                    min(current, previous)
                    if key not in self._LOWER_IS_LOOSER
                    else max(current, previous)
                )
                self._values[key] = _stored_number(stricter)
                self._record_refusal(key, "config/version mismatch; stricter value selected")
            if mismatch:
                self._startup_refused = True
                self._message(
                    "Settings/version mismatch detected; trading remains disabled "
                    "pending owner resolution."
                )
            return mismatch

    def _apply(self, applied: AppliedChange) -> None:
        normalized = _stored_number(applied.new_value)
        candidate = dict(self._values)
        candidate[applied.key] = normalized
        path = self._path_for(applied.key)
        snapshot = self._snapshot(path)
        try:
            if self._version_store is not None:
                self._version_store.append(applied)
            self._persist(applied.key, candidate)
        except Exception as exc:
            self._restore(path, snapshot)
            self._record(
                "settings_change_rejected", applied, reason=f"apply_failed:{type(exc).__name__}"
            )
            self._message(
                f"Change {applied.change_id} failed safely; current settings were unchanged."
            )
            raise SettingsRejected("setting change could not be persisted") from exc
        self._values[applied.key] = normalized
        self._applied[applied.change_id] = applied
        self._record("settings_change_applied", applied)
        self._message(f"Change {applied.change_id} applied: {applied.key}={normalized}.")

    def _persist(self, key: str, values: Mapping[str, Any]) -> None:
        path = self._path_for(key)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        current: dict[str, Any] = {}
        if not path.exists():
            default_path = Path(__file__).resolve().parents[3] / "config" / path.name
            if default_path.is_file():
                seeded = yaml.safe_load(default_path.read_text(encoding="utf-8"))
                if isinstance(seeded, dict):
                    current.update(seeded)
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current.update(loaded)
        target = current
        path_spec = self._KEY_PATHS.get(key, (path.name, (key,)))[1]
        for part in path_spec[:-1]:
            child = target.get(part)
            if not isinstance(child, dict):
                child = {}
                target[part] = child
            target = child
        target[path_spec[-1]] = _stored_number(values[key])
        contents = yaml.safe_dump(current, sort_keys=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            stream.write(contents)
            temporary = Path(stream.name)
        temporary.replace(path)

    def _path_for(self, key: str) -> Path | None:
        if self._config_path is not None:
            return self._config_path
        if self._config_dir is None:
            return None
        filename = self._KEY_PATHS.get(key, ("risk.yaml", ()))[0]
        return self._config_dir / filename

    @staticmethod
    def _snapshot(path: Path | None) -> tuple[bool, bytes] | None:
        if path is None or not path.exists():
            return None
        return True, path.read_bytes()

    @staticmethod
    def _restore(path: Path | None, snapshot: tuple[bool, bytes] | None) -> None:
        if path is None:
            return
        if snapshot is None:
            if path.exists():
                path.unlink()
            return
        path.write_bytes(snapshot[1])

    def _prune_expired(self) -> None:
        now = self._clock()
        expired = [
            change_id
            for change_id, change in self._pending.items()
            if now - change.requested_at > 300
        ]
        for change_id in expired:
            change = self._pending.pop(change_id)
            self._record("settings_change_rejected", change, reason="confirmation_expired")
            self._message(f"Change {change_id} refused: confirmation expired.")

    def _load_pending(self) -> None:
        if self._version_store is None:
            return
        loader = getattr(self._version_store, "load_pending", None)
        if loader is not None:
            for change in loader() or ():
                if isinstance(change, AppliedChange):
                    self._scheduled[change.change_id] = change

    def _save_pending(self) -> None:
        if self._version_store is None:
            return
        saver = getattr(self._version_store, "save_pending", None)
        if saver is not None:
            saver(tuple(self._scheduled.values()))

    def _request_number(self, value: object, key: str) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, Decimal | int | float):
            raise SettingsRejected("setting value must be int, float or Decimal")
        number = _number(value)
        if number <= 0:
            raise SettingsRejected("setting value must be greater than zero")
        if key in self._COUNT_KEYS and number != number.to_integral_value():
            raise SettingsRejected("count settings must be whole numbers")
        return number

    def _is_halted(self, requested: bool | None) -> bool:
        return bool(requested) if requested is not None else bool(self._halt_active())

    def _effective_at(self, now: float) -> float:
        if self._cooldown is not None:
            return now + self._cooldown
        current = datetime.fromtimestamp(now, UTC)
        next_day = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return next_day.timestamp()

    def _direction(self, key: str, new: Decimal, old: Decimal) -> str:
        return (
            ("loosening" if new < old else "tightening")
            if key in self._LOWER_IS_LOOSER
            else ("tightening" if new < old else "loosening")
        )

    def _check_bounds(self, key: str, value: Decimal) -> None:
        maximum = self._ceilings.get("max", {}).get(key)
        minimum = self._ceilings.get("min", {}).get(key)
        if minimum is None:
            minimum = self._ceilings.get("strategy_lab_floors", {}).get(key)
        if maximum is None and minimum is None:
            raise SettingsRejected(f"{key} has no baked ceiling or floor")
        if maximum is not None and value > _number(maximum):
            raise SettingsRejected(f"{key} exceeds baked ceiling {maximum}")
        if minimum is not None and value < _number(minimum):
            raise SettingsRejected(f"{key} is below baked floor {minimum}")

    def _check_consistency(self, key: str, value: Decimal) -> None:
        if key == "max_weekly_loss_pct" and value < _number(
            self._values.get("max_daily_loss_pct", value)
        ):
            raise SettingsRejected("weekly loss limit cannot be below daily loss limit")
        if key == "max_daily_loss_pct" and value > _number(
            self._values.get("max_weekly_loss_pct", value)
        ):
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
                self._message("TOTP loosening lockout activated for one hour after three failures.")
            raise SettingsRejected("invalid or replayed TOTP")
        self._last_totp_step, self._totp_failures = step, 0

    def _refuse(self, key: str, reason: str) -> None:
        self._record_refusal(key, reason)
        self._message(f"Setting change refused for {key}: {reason}.")

    def _record_refusal(self, key: str, reason: str) -> None:
        if self._audit is not None:
            self._audit.append(
                AuditEvent(
                    event_type="settings_change_rejected",
                    payload={
                        "key": key,
                        "reason": reason,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )
            )

    def _record(
        self, event_type: str, change: PendingChange | AppliedChange, *, reason: str = ""
    ) -> None:
        if self._audit is None:
            return
        payload: dict[str, Any] = {
            "change_id": change.change_id,
            "key": change.key,
            "old_value": str(change.old_value),
            "new_value": str(change.new_value),
            "direction": change.direction,
            "reason": reason or change.reason,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if isinstance(change, AppliedChange):
            payload.update({"approved_by": change.approved_by, "method": "owner_confirm"})
        self._audit.append(AuditEvent(event_type=event_type, payload=payload))

    def _message(self, text: str) -> None:
        if self._notify is not None:
            self._notify(text)

    @staticmethod
    def _expiry(change: PendingChange) -> str:
        return datetime.fromtimestamp(change.requested_at + 300, UTC).isoformat()
