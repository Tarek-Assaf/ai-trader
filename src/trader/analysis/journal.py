"""Safety-first journal for chart calls and later owner or data outcomes."""

from __future__ import annotations

import json
import math
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, insert, select, update

from trader.core.db import metadata, tables


class AnalysisJournalError(ValueError):
    """The journal rejected invalid or unsafe data."""


@dataclass(frozen=True, slots=True)
class AnalysisCall:
    """An immutable pre-outcome chart reading."""

    id: str
    created_at: datetime
    symbol: str
    timeframe: str
    source: str
    image_ref: str | None
    market_state: str
    bias: str
    key_levels: tuple[Any, ...]
    plan: Mapping[str, Any]
    reasons: str
    confidence: float
    status: str = "open"
    outcome: Mapping[str, Any] | None = None
    resolved_at: datetime | None = None
    lesson: str | None = None


class JournalReport(dict[str, Any]):
    """Dictionary report with a plain-language rendering and JSON export."""

    def __init__(self, data: Mapping[str, Any], *, plain_text: str) -> None:
        super().__init__(data)
        self.plain_text = plain_text

    @property
    def json(self) -> str:
        return json.dumps(self, sort_keys=True, default=str)

    def to_json(self) -> str:
        return self.json


OutcomeResolver = Callable[[AnalysisCall], Mapping[str, Any]]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _outcome_time(outcome: Mapping[str, Any] | None) -> datetime | None:
    if not outcome:
        return None
    for key in ("at", "timestamp", "outcome_at", "resolved_at"):
        value = outcome.get(key)
        if isinstance(value, datetime):
            return _utc(value)
        if isinstance(value, str):
            try:
                return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
            except ValueError:
                raise AnalysisJournalError("outcome timestamp is invalid") from None
    return None


def _validate_common(
    *,
    created_at: datetime,
    symbol: str,
    timeframe: str,
    source: str,
    image_ref: str | None,
    market_state: str,
    bias: str,
    key_levels: Iterable[Any],
    plan: Mapping[str, Any] | None,
    reasons: str,
    confidence: float,
    outcome: Mapping[str, Any] | None,
) -> tuple[datetime, tuple[Any, ...], dict[str, Any]]:
    created_at = _utc(created_at)
    if not symbol.strip() or not timeframe.strip():
        raise AnalysisJournalError("symbol and timeframe are required")
    if source not in {"screenshot", "recorded_data"}:
        raise AnalysisJournalError("source must be screenshot or recorded_data")
    if image_ref is not None and not image_ref.strip():
        raise AnalysisJournalError("image_ref cannot be blank")
    if market_state not in {"trend", "range", "transition", "unclear"}:
        raise AnalysisJournalError("market_state is invalid")
    if bias not in {"long", "short", "none"}:
        raise AnalysisJournalError("bias is invalid")
    if len(reasons) > 600:
        raise AnalysisJournalError("reasons must be at most 600 characters")
    if isinstance(confidence, bool) or not isinstance(confidence, (float, int)):
        raise AnalysisJournalError("confidence must be a number from 0 to 1")
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise AnalysisJournalError("confidence must be a number from 0 to 1")
    if not isinstance(key_levels, Iterable) or isinstance(key_levels, (str, bytes)):
        raise AnalysisJournalError("key_levels must be a list")
    if plan is not None and not isinstance(plan, Mapping):
        raise AnalysisJournalError("plan must be an object")
    if outcome is not None and not isinstance(outcome, Mapping):
        raise AnalysisJournalError("outcome must be an object")
    outcome_time = _outcome_time(outcome)
    if outcome_time is not None and outcome_time < created_at:
        raise AnalysisJournalError("a call cannot be created after its supplied outcome")
    return created_at, tuple(key_levels), dict(plan or {})


def _is_hit(outcome: Mapping[str, Any]) -> bool | None:
    value = outcome.get("hit", outcome.get("result"))
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value > 0
    if isinstance(value, str):
        normalized = value.casefold().strip()
        if normalized in {"hit", "win", "won", "profit", "profitable", "success"}:
            return True
        if normalized in {"miss", "loss", "lost", "losses", "fail", "failed", "void"}:
            return False
    return None


def _r_value(outcome: Mapping[str, Any]) -> float | None:
    value = outcome.get("r", outcome.get("result_r"))
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class AnalysisJournal:
    """Store pre-outcome calls in memory or in the durable analysis_journal table."""

    def __init__(
        self,
        engine: Engine | None = None,
        *,
        outcome_resolver: OutcomeResolver | None = None,
    ) -> None:
        self._engine = engine
        self._outcome_resolver = outcome_resolver
        self._calls: dict[str, AnalysisCall] = {}
        if engine is not None:
            metadata.create_all(engine, tables=[tables["analysis_journal"]])

    def add_call(
        self,
        *,
        created_at: datetime,
        symbol: str,
        timeframe: str,
        source: str,
        image_ref: str | None = None,
        market_state: str,
        bias: str,
        key_levels: Iterable[Any] = (),
        plan: Mapping[str, Any] | None = None,
        reasons: str = "",
        confidence: float,
        outcome: Mapping[str, Any] | None = None,
        call_id: str | None = None,
    ) -> AnalysisCall:
        created_at, key_levels, plan = _validate_common(
            created_at=created_at,
            symbol=symbol,
            timeframe=timeframe,
            source=source,
            image_ref=image_ref,
            market_state=market_state,
            bias=bias,
            key_levels=key_levels,
            plan=plan,
            reasons=reasons,
            confidence=confidence,
            outcome=outcome,
        )
        identifier = call_id or str(uuid.uuid4())
        if identifier in self._calls or self._load(identifier) is not None:
            raise AnalysisJournalError("call id already exists")
        call = AnalysisCall(
            id=identifier,
            created_at=created_at,
            symbol=symbol.strip(),
            timeframe=timeframe.strip(),
            source=source,
            image_ref=image_ref.strip() if image_ref else None,
            market_state=market_state,
            bias=bias,
            key_levels=key_levels,
            plan=plan,
            reasons=reasons,
            confidence=float(confidence),
            outcome=dict(outcome) if outcome else None,
            status=(
                "void"
                if outcome and str(outcome.get("result", "")).casefold() == "void"
                else "resolved"
                if outcome
                else "open"
            ),
            resolved_at=_outcome_time(outcome) if outcome else None,
        )
        if self._engine is None:
            self._calls[identifier] = call
        else:
            self._insert(call)
        return call

    def resolve(
        self,
        call_id: str,
        outcome: Mapping[str, Any] | None = None,
        *,
        lesson: str | None = None,
    ) -> AnalysisCall:
        call = self.get(call_id)
        if call is None:
            raise AnalysisJournalError("analysis call was not found")
        if call.status != "open":
            raise AnalysisJournalError("analysis call can only be resolved once")
        if outcome is None and self._outcome_resolver is not None:
            outcome = self._outcome_resolver(call)
        if not isinstance(outcome, Mapping) or not outcome:
            raise AnalysisJournalError("outcome is required")
        resolved_at = _outcome_time(outcome) or datetime.now(UTC)
        if resolved_at < call.created_at:
            raise AnalysisJournalError("outcome cannot predate the analysis call")
        outcome_value = str(outcome.get("result", "")).casefold()
        resolution_status = "void" if outcome_value == "void" else "resolved"
        resolved = AnalysisCall(
            id=call.id,
            created_at=call.created_at,
            symbol=call.symbol,
            timeframe=call.timeframe,
            source=call.source,
            image_ref=call.image_ref,
            market_state=call.market_state,
            bias=call.bias,
            key_levels=call.key_levels,
            plan=call.plan,
            reasons=call.reasons,
            confidence=call.confidence,
            status=resolution_status,
            outcome=dict(outcome),
            resolved_at=resolved_at,
            lesson=lesson,
        )
        if self._engine is None:
            self._calls[call_id] = resolved
        else:
            self._update(resolved)
        return resolved

    def resolve_from_recorded_data(
        self, call_id: str, resolver: OutcomeResolver | None = None, *, lesson: str | None = None
    ) -> AnalysisCall:
        """Resolve a recorded-data call through a caller-supplied lake calculation."""
        call = self.get(call_id)
        if call is None:
            raise AnalysisJournalError("analysis call was not found")
        if call.source != "recorded_data":
            raise AnalysisJournalError("recorded-data resolution requires a recorded_data call")
        selected = resolver or self._outcome_resolver
        if selected is None:
            raise AnalysisJournalError("a recorded-data outcome resolver is required")
        return self.resolve(call_id, selected(call), lesson=lesson)

    def get(self, call_id: str) -> AnalysisCall | None:
        if self._engine is None:
            return self._calls.get(call_id)
        return self._load(call_id)

    def calls(self) -> tuple[AnalysisCall, ...]:
        if self._engine is None:
            return tuple(self._calls.values())
        table = tables["analysis_journal"]
        with self._engine.connect() as connection:
            rows = connection.execute(select(table)).mappings().all()
        return tuple(self._from_row(row) for row in rows)

    def report(self) -> JournalReport:
        calls = self.calls()
        resolved = [call for call in calls if call.status == "resolved" and call.outcome]
        report: dict[str, Any] = {
            "total_calls": len(calls),
            "resolved_calls": len(resolved),
            "open_calls": sum(call.status == "open" for call in calls),
            "void_calls": sum(call.status == "void" for call in calls),
            "by_confidence": self._group(
                resolved, lambda call: self._confidence_bucket(call.confidence)
            ),
            "by_market_state": self._group(resolved, lambda call: call.market_state),
            "by_symbol_timeframe": self._group(
                resolved, lambda call: f"{call.symbol}/{call.timeframe}"
            ),
            "lessons": [call.lesson for call in resolved if call.lesson],
        }
        lines = [
            f"{len(resolved)} of {len(calls)} analysis calls are resolved; "
            f"{report['open_calls']} remain open.",
        ]
        for label, groups in (
            ("confidence", report["by_confidence"]),
            ("market state", report["by_market_state"]),
            ("symbol/timeframe", report["by_symbol_timeframe"]),
        ):
            if groups:
                lines.append(f"By {label}: " + "; ".join(
                    f"{key}: hit rate {value['hit_rate']:.1%}, mean R {value['mean_r']:.2f}"
                    for key, value in groups.items()
                ))
        return JournalReport(report, plain_text="\n".join(lines))

    @staticmethod
    def _confidence_bucket(confidence: float) -> str:
        if confidence < 1 / 3:
            return "low"
        if confidence < 2 / 3:
            return "medium"
        return "high"

    @staticmethod
    def _group(calls: Iterable[AnalysisCall], key: Callable[[AnalysisCall], str]) -> dict[str, Any]:
        buckets: dict[str, list[AnalysisCall]] = defaultdict(list)
        for call in calls:
            buckets[key(call)].append(call)
        result: dict[str, Any] = {}
        for name, entries in sorted(buckets.items()):
            hits = [
                hit
                for hit in (_is_hit(call.outcome or {}) for call in entries)
                if hit is not None
            ]
            rs = [
                value
                for value in (_r_value(call.outcome or {}) for call in entries)
                if value is not None
            ]
            result[name] = {
                "calls": len(entries),
                "hits": sum(hits),
                "hit_rate": sum(hits) / len(hits) if hits else 0.0,
                "mean_r": sum(rs) / len(rs) if rs else 0.0,
                "r_samples": len(rs),
            }
        return result

    def _insert(self, call: AnalysisCall) -> None:
        table = tables["analysis_journal"]
        values = self._values(call)
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(table).values(**values))
        except Exception as exc:
            raise AnalysisJournalError("analysis call persistence failed") from exc

    def _update(self, call: AnalysisCall) -> None:
        table = tables["analysis_journal"]
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    update(table)
                    .where(table.c.id == call.id)
                    .values(
                        status=call.status,
                        outcome=_json_value(call.outcome),
                        resolved_at=call.resolved_at,
                        lesson=call.lesson,
                    )
                )
        except Exception as exc:
            raise AnalysisJournalError("analysis outcome persistence failed") from exc

    def _load(self, call_id: str) -> AnalysisCall | None:
        table = tables["analysis_journal"]
        with self._engine.connect() as connection:
            row = connection.execute(select(table).where(table.c.id == call_id)).mappings().first()
        return self._from_row(row) if row else None

    @staticmethod
    def _values(call: AnalysisCall) -> dict[str, Any]:
        return {
            "id": call.id,
            "created_at": call.created_at,
            "symbol": call.symbol,
            "timeframe": call.timeframe,
            "source": call.source,
            "image_ref": call.image_ref,
            "market_state": call.market_state,
            "bias": call.bias,
            "key_levels": _json_value(call.key_levels),
            "plan": _json_value(call.plan),
            "reasons": call.reasons,
            "confidence": call.confidence,
            "status": call.status,
            "outcome": _json_value(call.outcome),
            "resolved_at": call.resolved_at,
            "lesson": call.lesson,
        }

    @staticmethod
    def _from_row(row: Mapping[str, Any]) -> AnalysisCall:
        return AnalysisCall(
            id=str(row["id"]),
            created_at=_utc(row["created_at"]),
            symbol=str(row["symbol"]),
            timeframe=str(row["timeframe"]),
            source=str(row["source"]),
            image_ref=row["image_ref"],
            market_state=str(row["market_state"]),
            bias=str(row["bias"]),
            key_levels=tuple(row["key_levels"] or []),
            plan=dict(row["plan"] or {}),
            reasons=str(row["reasons"] or ""),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            outcome=dict(row["outcome"]) if row["outcome"] else None,
            resolved_at=_utc(row["resolved_at"]) if row["resolved_at"] else None,
            lesson=row["lesson"],
        )


class InMemoryAnalysisJournal(AnalysisJournal):
    """Explicit SQLite-free journal for unit tests and offline chat flows."""

    def __init__(self, *, outcome_resolver: OutcomeResolver | None = None) -> None:
        super().__init__(None, outcome_resolver=outcome_resolver)


Journal = AnalysisJournal
InMemoryJournal = InMemoryAnalysisJournal


# The table is declared here so the migration and metadata-backed SQLite tests share its shape.
# It is registered by core.db alongside the other persistence tables.
