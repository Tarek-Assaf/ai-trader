"""Record a paper/demo soak and emit secret-free incident evidence.

The monitor is deliberately independent of the broker, LLM and Telegram.  The
runtime supplies observations as data; this module counts them and applies
deterministic safety rules.  An incident calls the injected alert function,
writes one JSON evidence file and resets the four-week clock.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

LOGGER = logging.getLogger(__name__)
_SECRET_KEY = re.compile(r"(?:token|secret|password|passwd|api[_-]?key|chat[_-]?id)", re.I)
_SECRET_VALUE = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|bot\d+:[A-Za-z0-9_-]{20,})")


@dataclass(slots=True)
class SoakDay:
    """Counters and account facts for one UTC calendar day."""

    day: date
    cycles: int = 0
    intents: int = 0
    guardian_rejections: Counter[str] = field(default_factory=Counter)
    orders: int = 0
    fills: int = 0
    reconciliation_mismatches: int = 0
    alerts: int = 0
    uptime_seconds: float = 0.0
    restarts: int = 0
    llm_spend: Decimal = Decimal("0")
    equity: Decimal | None = None
    drawdown_pct: Decimal | None = None

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["day"] = self.day.isoformat()
        value["guardian_rejections"] = dict(self.guardian_rejections)
        value["llm_spend"] = str(self.llm_spend)
        if self.equity is not None:
            value["equity"] = str(self.equity)
        if self.drawdown_pct is not None:
            value["drawdown_pct"] = str(self.drawdown_pct)
        return value


@dataclass(frozen=True, slots=True)
class Incident:
    """A detected rule breach and its sanitized observation payload."""

    rule: str
    occurred_at: datetime
    details: Mapping[str, object]
    evidence_path: str


class SoakMonitor:
    """Collect soak metrics and evaluate all Phase 8 incident rules."""

    RULES = (
        "order_without_server_side_stop",
        "open_position_without_broker_stop",
        "reconciliation_mismatch_unhealed",
        "risk_per_trade_above_limit",
        "daily_loss_limit_exceeded",
        "control_switch_not_honored",
        "secret_found_in_logs",
    )

    def __init__(
        self,
        evidence_dir: str | Path = "docs/evidence/soak",
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        alert: Callable[[str], None] | None = None,
        secret_patterns: Sequence[str] = (),
    ) -> None:
        self.evidence_dir = Path(evidence_dir)
        self._clock = clock
        self._alert = alert or (lambda message: LOGGER.error("soak incident: %s", message))
        self._secret_patterns = tuple(pattern for pattern in secret_patterns if pattern)
        self._days: dict[date, SoakDay] = {}
        self._incidents: list[Incident] = []
        self._active_rules: set[str] = set()
        self._clock_started_at = self._now()
        self._lock = threading.RLock()

    @property
    def incidents(self) -> tuple[Incident, ...]:
        with self._lock:
            return tuple(self._incidents)

    @property
    def postmortem_required(self) -> bool:
        return bool(self._incidents)

    @property
    def clock_started_at(self) -> datetime:
        return self._clock_started_at

    @property
    def days_run(self) -> int:
        with self._lock:
            return len(self._days)

    def record_cycle(self, *, at: datetime | None = None) -> None:
        self._day(at).cycles += 1

    def record_intent(self, *, at: datetime | None = None) -> None:
        self._day(at).intents += 1

    def record_guardian_rejection(self, rule: str, *, at: datetime | None = None) -> None:
        if not rule.strip():
            raise ValueError("guardian rejection rule must be non-empty")
        self._day(at).guardian_rejections[rule] += 1

    def record_order(self, *, at: datetime | None = None) -> None:
        self._day(at).orders += 1

    def record_fill(self, *, at: datetime | None = None) -> None:
        self._day(at).fills += 1

    def record_reconciliation_mismatch(self, *, at: datetime | None = None) -> None:
        self._day(at).reconciliation_mismatches += 1

    def record_alert(self, *, at: datetime | None = None) -> None:
        self._day(at).alerts += 1

    def record_restart(self, *, at: datetime | None = None) -> None:
        self._day(at).restarts += 1

    def record_uptime(self, seconds: float, *, at: datetime | None = None) -> None:
        if not isinstance(seconds, int | float) or seconds < 0:
            raise ValueError("uptime seconds must be non-negative")
        self._day(at).uptime_seconds += float(seconds)

    def record_llm_spend(
        self, amount: Decimal | str | int | float, *, at: datetime | None = None
    ) -> None:
        value = Decimal(str(amount))
        if not value.is_finite() or value < 0:
            raise ValueError("LLM spend must be non-negative and finite")
        self._day(at).llm_spend += value

    def record_equity(
        self,
        equity: Decimal | str | int | float,
        *,
        drawdown_pct: Decimal | str | int | float | None = None,
        at: datetime | None = None,
    ) -> None:
        value = Decimal(str(equity))
        if not value.is_finite() or value <= 0:
            raise ValueError("equity must be positive and finite")
        day = self._day(at)
        day.equity = value
        day.drawdown_pct = None if drawdown_pct is None else Decimal(str(drawdown_pct))

    def evaluate(self, observation: Mapping[str, object]) -> tuple[Incident, ...]:
        """Evaluate one observation and emit at most one incident per rule.

        Repeating the same rule while a single condition remains true does not
        create an evidence storm; a new incident is emitted after the rule has
        returned to a safe state.
        """
        if not isinstance(observation, Mapping):
            raise TypeError("soak observation must be a mapping")
        checks = self._checks(observation)
        present_rules = {rule for rule, _details in checks}
        self._active_rules.intersection_update(present_rules)
        created: list[Incident] = []
        for rule, details in checks:
            if rule in self._active_rules:
                continue
            created.append(self._emit(rule, details))
            self._active_rules.add(rule)
        return tuple(created)

    def summary(self) -> dict[str, object]:
        """Return sanitized summary data suitable for Telegram or an API."""
        with self._lock:
            now = self._now()
            elapsed = max(0, (now - self._clock_started_at).total_seconds())
            return {
                "days_run": len(self._days),
                "incidents": len(self._incidents),
                "incident_rules": dict(Counter(item.rule for item in self._incidents)),
                "clock_started_at": self._clock_started_at.isoformat(),
                "clock_days": int(elapsed // 86_400),
                "four_week_complete": elapsed >= 28 * 86_400 and not self._incidents,
                "postmortem_required": bool(self._incidents),
                "days": [self._days[key].as_dict() for key in sorted(self._days)],
            }

    def summary_command(self) -> str:
        value = self.summary()
        return (
            f"Soak: {value['days_run']} days run; {value['incidents']} incidents; "
            f"clock {value['clock_days']}/28 days; "
            f"post-mortem {'required' if value['postmortem_required'] else 'not required'}."
        )

    def command(self, command: str = "/soak") -> str:
        if command.strip().split(maxsplit=1)[0].lower() != "/soak":
            raise ValueError("unsupported soak command")
        return self.summary_command()

    def _checks(
        self, observation: Mapping[str, object]
    ) -> tuple[tuple[str, Mapping[str, object]], ...]:
        checks: list[tuple[str, Mapping[str, object]]] = []
        if observation.get("order_without_server_side_stop") is True:
            checks.append(("order_without_server_side_stop", {"detected": True}))
        if observation.get("open_position_without_broker_stop") is True:
            checks.append(("open_position_without_broker_stop", {"detected": True}))
        age = observation.get("reconciliation_mismatch_age_seconds")
        if age is not None and float(age) >= 300:
            checks.append(("reconciliation_mismatch_unhealed", {"age_seconds": float(age)}))
        risk = observation.get("risk_per_trade_pct")
        limit = observation.get("risk_per_trade_limit_pct")
        if risk is not None and limit is not None and Decimal(str(risk)) > Decimal(str(limit)):
            checks.append(
                ("risk_per_trade_above_limit", {"risk_pct": str(risk), "limit_pct": str(limit)})
            )
        loss = observation.get("daily_loss_pct")
        loss_limit = observation.get("daily_loss_limit_pct")
        if (
            loss is not None
            and loss_limit is not None
            and Decimal(str(loss)) >= Decimal(str(loss_limit))
        ):
            checks.append(
                ("daily_loss_limit_exceeded", {"loss_pct": str(loss), "limit_pct": str(loss_limit)})
            )
        requested = observation.get("control_requested")
        honored = observation.get("control_honored")
        if isinstance(requested, str) and requested in {"kill", "pause"} and honored is False:
            checks.append(("control_switch_not_honored", {"requested": requested}))
        log_text = str(observation.get("log_text", ""))
        if any(secret in log_text for secret in self._secret_patterns) or _SECRET_VALUE.search(
            log_text
        ):
            checks.append(("secret_found_in_logs", {"detected": True}))
        return tuple(checks)

    def _emit(self, rule: str, details: Mapping[str, object]) -> Incident:
        now = self._now()
        safe_details = _sanitize(details)
        if not isinstance(safe_details, Mapping):
            safe_details = {}
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.evidence_dir / (
            f"incident-{now.strftime('%Y%m%dT%H%M%SZ')}-{len(self._incidents) + 1}.json"
        )
        payload = {"rule": rule, "occurred_at": now.isoformat(), "details": safe_details}
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        incident = Incident(rule, now, safe_details, str(path))
        self._incidents.append(incident)
        self._clock_started_at = now
        self._day(now).alerts += 1
        try:
            self._alert(rule)
        except Exception:
            LOGGER.exception("soak incident alert failed")
        return incident

    def _day(self, at: datetime | None) -> SoakDay:
        stamp = self._now() if at is None else _aware(at)
        with self._lock:
            return self._days.setdefault(stamp.date(), SoakDay(stamp.date()))

    def _now(self) -> datetime:
        return _aware(self._clock())


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("soak timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _sanitize(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str | int | float | bool) or value is None:
        return "[REDACTED]" if isinstance(value, str) and _SECRET_VALUE.search(value) else value
    return str(value)


__all__ = ["Incident", "SoakDay", "SoakMonitor"]
