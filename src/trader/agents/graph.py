"""Bounded event-driven agent graph; trade proposals never size or execute themselves."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from importlib.resources import files
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trader.agents.llm.client import LLMClient
from trader.agents.llm.types import (
    BudgetExceeded,
    InvalidModelOutput,
    LLMError,
    Message,
    ModelUnavailable,
)
from trader.core.audit import AuditEvent, AuditWriter
from trader.research.sanitizer import ResearchItem

TModel = TypeVar("TModel", bound=BaseModel)

Event = Literal[
    "signal", "bar_close", "news_event", "level_reached", "position_event", "session_open"
]
PROMPTS = {
    "data_summarizer": "data_summarizer.md",
    "technical_analyst": "technical_analyst.md",
    "news_macro_analyst": "news_macro_analyst.md",
    "strategist": "strategist.md",
    "critic": "critic.md",
}
MAX_REVISIONS = 2


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Summary(StrictModel):
    provenance_id: str
    summary: str = Field(min_length=1, max_length=1000)
    flags: tuple[str, ...] = ()


class SummaryBatch(StrictModel):
    items: tuple[Summary, ...] = ()
    flags: tuple[str, ...] = ()


class TechnicalReport(StrictModel):
    bias: Literal["long", "short", "neutral"]
    confidence: float = Field(ge=0, le=1)
    regime: str
    timeframe_agreement: str
    key_levels: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()


class EventRisk(StrictModel):
    time: str
    impact: str
    instrument_relevance: str


class Claim(StrictModel):
    text: str
    provenance_ids: tuple[str, ...] = ()
    verified: bool


class MacroReport(StrictModel):
    event_risk: Literal["none", "elevated", "blackout_advised"]
    upcoming_events: tuple[EventRisk, ...] = ()
    backdrop: tuple[str, ...] = Field(max_length=5)
    claims: tuple[Claim, ...] = ()
    flags: tuple[str, ...] = ()


class TradeIntent(StrictModel):
    instrument: str = Field(min_length=1, max_length=32)
    direction: Literal["BUY", "SELL"]
    entry_type: Literal["market", "limit", "stop"]
    entry_level: Decimal = Field(gt=0)
    stop_level: Decimal = Field(gt=0)
    target_levels: tuple[Decimal, ...] = Field(min_length=1, max_length=5)
    time_horizon: str = Field(min_length=1, max_length=80)
    thesis: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0, le=1)


class Proposal(StrictModel):
    action: Literal["TRADE", "NO_TRADE"]
    intent: TradeIntent | None = None
    thesis: str = Field(max_length=600)
    confidence: float = Field(ge=0, le=1)
    no_trade_reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_action(self) -> Proposal:
        if (self.action == "TRADE") != (self.intent is not None):
            raise ValueError("TRADE requires an intent; NO_TRADE must not have one")
        if self.action == "NO_TRADE" and not self.no_trade_reason:
            raise ValueError("NO_TRADE requires a reason")
        if self.intent and self.intent.thesis != self.thesis:
            raise ValueError("proposal thesis must match intent thesis")
        return self


class Critique(StrictModel):
    verdict: Literal["approve", "revise", "veto"]
    objections: tuple[str, ...] = ()
    strongest_reason: str = Field(max_length=300)
    flags: tuple[str, ...] = ()


class GuardianDecision(StrictModel):
    approved: bool
    approved_size: Decimal | None = Field(default=None, gt=0)
    reasons: tuple[str, ...] = ()
    details: Mapping[str, Any] = Field(default_factory=dict)


class ExecutionOutcome(StrictModel):
    status: Literal["submitted", "rejected", "failed"]
    reference: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=300)


class CycleInput(StrictModel):
    cycle_id: str = Field(min_length=1, max_length=128)
    event: Event
    event_id: str = Field(min_length=1, max_length=128)
    instrument: str = Field(min_length=1, max_length=32)
    snapshot_json: str = Field(min_length=2, max_length=100_000)
    research: tuple[ResearchItem, ...] = Field(max_length=8)
    guardian_context: Any = Field(exclude=True)


class CycleBudget(StrictModel):
    max_steps: int = Field(default=12, ge=1, le=32)
    max_model_calls: int = Field(default=12, ge=1, le=32)
    max_tokens: int = Field(default=8_000, ge=1)
    wall_seconds: float = Field(default=30.0, gt=0, le=120)


class Guardian(Protocol):
    async def evaluate(
        self, intent: TradeIntent, context: object, cycle_id: str
    ) -> GuardianDecision: ...


class Executor(Protocol):
    async def execute(
        self, intent: TradeIntent, decision: GuardianDecision, cycle_id: str
    ) -> ExecutionOutcome: ...


@dataclass(frozen=True, slots=True)
class CycleResult:
    action: Literal["TRADE", "NO_TRADE"]
    reason: str
    proposal: Proposal | None = None
    guardian: GuardianDecision | None = None
    execution: ExecutionOutcome | None = None
    model_calls: int = 0
    tokens: int = 0
    flags: tuple[str, ...] = ()


def _untrusted_derived_text(value: str, provenance_ids: set[str]) -> str:
    ids = html.escape(",".join(sorted(provenance_ids)), quote=True)
    text = html.escape(value, quote=False)
    return f'<untrusted_content source="derived" provenance_ids="{ids}">{text}</untrusted_content>'


def _summary_context(report: SummaryBatch) -> dict[str, Any]:
    payload = report.model_dump(mode="json")
    for item in payload["items"]:
        provenance = {item["provenance_id"]}
        item["summary"] = _untrusted_derived_text(item["summary"], provenance)
        item["flags"] = [_untrusted_derived_text(flag, provenance) for flag in item["flags"]]
    payload["flags"] = [_untrusted_derived_text(flag, set()) for flag in payload["flags"]]
    return payload


def _technical_context(report: TechnicalReport, provenance_ids: set[str]) -> dict[str, Any]:
    payload = report.model_dump(mode="json")
    for field in ("regime", "timeframe_agreement"):
        payload[field] = _untrusted_derived_text(payload[field], provenance_ids)
    for field in ("key_levels", "risks", "flags"):
        payload[field] = [
            _untrusted_derived_text(value, provenance_ids) for value in payload[field]
        ]
    return payload


def _macro_context(report: MacroReport, provenance_ids: set[str]) -> dict[str, Any]:
    payload = report.model_dump(mode="json")
    payload["backdrop"] = [
        _untrusted_derived_text(value, provenance_ids) for value in payload["backdrop"]
    ]
    payload["flags"] = [
        _untrusted_derived_text(value, provenance_ids) for value in payload["flags"]
    ]
    for event in payload["upcoming_events"]:
        for field, value in event.items():
            event[field] = _untrusted_derived_text(value, provenance_ids)
    for claim in payload["claims"]:
        claim["text"] = _untrusted_derived_text(claim["text"], set(claim["provenance_ids"]))
    return payload


def _proposal_context(proposal: Proposal, provenance_ids: set[str]) -> dict[str, Any]:
    payload = proposal.model_dump(mode="json")
    payload["thesis"] = _untrusted_derived_text(payload["thesis"], provenance_ids)
    if payload["no_trade_reason"]:
        payload["no_trade_reason"] = _untrusted_derived_text(
            payload["no_trade_reason"], provenance_ids
        )
    if payload["intent"] is not None:
        for field in ("thesis", "time_horizon"):
            payload["intent"][field] = _untrusted_derived_text(
                payload["intent"][field], provenance_ids
            )
    return payload


def _critique_context(critique: Critique, provenance_ids: set[str]) -> dict[str, Any]:
    payload = critique.model_dump(mode="json")
    payload["strongest_reason"] = _untrusted_derived_text(
        payload["strongest_reason"], provenance_ids
    )
    for field in ("objections", "flags"):
        payload[field] = [
            _untrusted_derived_text(value, provenance_ids) for value in payload[field]
        ]
    return payload


class PromptCatalog:
    """Only load checked-in prompts by a fixed role map."""

    def __init__(self) -> None:
        self.root = files("trader.agents.prompts")
        self.common = self.root.joinpath("common_rules.md").read_text(encoding="utf-8")
        self.prompts = {
            role: self.root.joinpath(path).read_text(encoding="utf-8")
            for role, path in PROMPTS.items()
        }

    def get(self, role: str, instrument: str) -> tuple[str, str, int]:
        if role not in self.prompts:
            raise ValueError("unknown prompt role")
        prompt = self.prompts[role]
        match = re.search(r"^# .*?version (\d+)", prompt, re.I | re.M)
        if not match:
            raise ValueError("prompt version is missing")
        if role in {"technical_analyst", "news_macro_analyst", "strategist"}:
            name = {"NAS100": "playbook_nas100.md", "XAUUSD": "playbook_gold.md"}.get(
                instrument.upper()
            )
            if name:
                prompt += "\n\n" + self.root.joinpath(name).read_text(encoding="utf-8")
        return self.common + "\n\n" + prompt, PROMPTS[role], int(match.group(1))


class AgentGraph:
    """Run one event cycle. LLM text is data; there is no model-facing tool dispatcher."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        audit: AuditWriter,
        guardian: Guardian,
        executor: Executor,
        budget: CycleBudget | None = None,
        prompts: PromptCatalog | None = None,
    ) -> None:
        self.llm, self.audit, self.guardian, self.executor = llm, audit, guardian, executor
        self.budget, self.prompts = budget or CycleBudget(), prompts or PromptCatalog()

    async def run(self, cycle: CycleInput) -> CycleResult:
        started = time.monotonic()
        calls = tokens = steps = 0
        try:
            snapshot = json.loads(cycle.snapshot_json)
            if not isinstance(snapshot, dict):
                return self._no_trade(cycle, "invalid_snapshot", calls, tokens)
            ids = {item.id for item in cycle.research}
            if len(ids) != len(cycle.research):
                return self._no_trade(cycle, "duplicate_provenance_id", calls, tokens)
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "event": cycle.event,
                        "event_id": cycle.event_id,
                        "instrument": cycle.instrument,
                        "snapshot": snapshot,
                        "research": [
                            {"id": item.id, "sha256": item.content_hash} for item in cycle.research
                        ],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()
            self._audit(
                cycle,
                "agent_cycle_started",
                {"event": cycle.event, "instrument": cycle.instrument, "input_sha256": digest},
            )

            summaries, used = await self._ask(
                cycle,
                started,
                "data_summarizer",
                SummaryBatch,
                {
                    "items": [item.untrusted_block for item in cycle.research],
                    "provenance_ids": sorted(ids),
                },
            )
            calls, tokens, steps = calls + 1, tokens + used, steps + 1
            if self._exhausted(started, calls, tokens, steps):
                return self._no_trade(cycle, "cycle_budget_exhausted", calls, tokens)
            if any(item.provenance_id not in ids for item in summaries.items):
                return self._no_trade(cycle, "unknown_provenance", calls, tokens)

            technical, used = await self._ask(
                cycle,
                started,
                "technical_analyst",
                TechnicalReport,
                {"instrument": cycle.instrument, "snapshot": snapshot},
            )
            calls, tokens, steps = calls + 1, tokens + used, steps + 1
            if self._exhausted(started, calls, tokens, steps):
                return self._no_trade(cycle, "cycle_budget_exhausted", calls, tokens)

            macro, used = await self._ask(
                cycle,
                started,
                "news_macro_analyst",
                MacroReport,
                {
                    "instrument": cycle.instrument,
                    "research": [i.untrusted_block for i in cycle.research],
                    "summaries": _summary_context(summaries),
                    "provenance_ids": sorted(ids),
                },
            )
            calls, tokens, steps = calls + 1, tokens + used, steps + 1
            if self._exhausted(started, calls, tokens, steps):
                return self._no_trade(cycle, "cycle_budget_exhausted", calls, tokens)
            if any(not set(claim.provenance_ids).issubset(ids) for claim in macro.claims):
                return self._no_trade(cycle, "unknown_provenance", calls, tokens)

            technical_context = _technical_context(technical, ids)
            macro_context = _macro_context(macro, ids)
            summary_context = _summary_context(summaries)
            critique: Critique | None = None
            proposal: Proposal | None = None
            for revision in range(3):
                strategy_input = {
                    "instrument": cycle.instrument,
                    "snapshot": snapshot,
                    "technical_report": technical_context,
                    "macro_report": macro_context,
                    "research_summaries": summary_context,
                    "critic_feedback": _critique_context(critique, ids) if critique else None,
                }
                proposal, used = await self._ask(
                    cycle, started, "strategist", Proposal, strategy_input
                )
                calls, tokens, steps = calls + 1, tokens + used, steps + 1
                if self._exhausted(started, calls, tokens, steps):
                    return self._no_trade(cycle, "cycle_budget_exhausted", calls, tokens, proposal)
                if proposal.action == "NO_TRADE":
                    return self._no_trade(
                        cycle,
                        proposal.no_trade_reason or "strategist_no_trade",
                        calls,
                        tokens,
                        proposal,
                    )
                assert proposal.intent is not None
                critique, used = await self._ask(
                    cycle,
                    started,
                    "critic",
                    Critique,
                    {
                        "proposal": _proposal_context(proposal, ids),
                        "technical_report": technical_context,
                        "macro_report": macro_context,
                    },
                )
                calls, tokens, steps = calls + 1, tokens + used, steps + 1
                if self._exhausted(started, calls, tokens, steps):
                    return self._no_trade(cycle, "cycle_budget_exhausted", calls, tokens, proposal)
                if critique.verdict == "veto":
                    return self._no_trade(
                        cycle, critique.strongest_reason or "critic_veto", calls, tokens, proposal
                    )
                if critique.verdict == "approve":
                    break
                if revision == 2:
                    return self._no_trade(cycle, "critique_revision_limit", calls, tokens, proposal)

            if proposal is None or proposal.intent is None:
                return self._no_trade(cycle, "missing_trade_proposal", calls, tokens, proposal)
            if self._exhausted(started, calls, tokens, steps):
                return self._no_trade(cycle, "cycle_budget_exhausted", calls, tokens, proposal)
            if proposal.intent.instrument.strip().upper() != cycle.instrument.strip().upper():
                return self._no_trade(cycle, "instrument_mismatch", calls, tokens, proposal)

            decision = await asyncio.wait_for(
                self.guardian.evaluate(proposal.intent, cycle.guardian_context, cycle.cycle_id),
                timeout=self._remaining(started),
            )
            self._audit(
                cycle,
                "agent_guardian_decision",
                {
                    "proposal": proposal.model_dump(mode="json"),
                    "approved": decision.approved,
                    "approved_size": str(decision.approved_size)
                    if decision.approved_size
                    else None,
                    "reasons": list(decision.reasons),
                    "details": dict(decision.details),
                },
            )
            if not decision.approved or decision.approved_size is None:
                return self._no_trade(cycle, "guardian_rejected", calls, tokens, proposal, decision)
            result = await asyncio.wait_for(
                self.executor.execute(proposal.intent, decision, cycle.cycle_id),
                timeout=self._remaining(started),
            )
            audit_flags: tuple[str, ...] = ()
            try:
                self._audit(
                    cycle,
                    "agent_execution_result",
                    {
                        "status": result.status,
                        "reference": result.reference,
                        "reason": result.reason,
                    },
                )
            except Exception:
                audit_flags = ("audit_write_failed",)
            return CycleResult(
                "TRADE" if result.status == "submitted" else "NO_TRADE",
                (
                    "execution_submitted"
                    if result.status == "submitted"
                    else "execution_not_submitted"
                ),
                proposal,
                decision,
                result,
                calls,
                tokens,
                audit_flags,
            )
        except BudgetExceeded:
            reason = "llm_budget_exceeded"
        except ModelUnavailable:
            reason = "llm_model_unavailable"
        except InvalidModelOutput:
            reason = "llm_invalid_output"
        except TimeoutError:
            reason = "cycle_wall_clock_exhausted"
        except LLMError:
            reason = "llm_failure"
        except Exception:
            reason = "cycle_failed_closed"
        return self._no_trade(cycle, reason, calls, tokens)

    async def _ask(
        self,
        cycle: CycleInput,
        started: float,
        role: str,
        schema: type[TModel],
        payload: Mapping[str, Any],
    ) -> tuple[TModel, int]:
        system, filename, version = self.prompts.get(role, cycle.instrument)
        messages = (
            Message("system", system),
            Message(
                "user", json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
            ),
        )
        self._audit(
            cycle,
            "agent_model_input",
            {
                "role": role,
                "prompt_file": filename,
                "prompt_version": version,
                "messages": [{"role": item.role, "content": item.content} for item in messages],
            },
        )
        result = await asyncio.wait_for(
            self.llm.complete(role, messages, schema), timeout=self._remaining(started)
        )
        self._audit(
            cycle,
            "agent_model_output",
            {
                "role": role,
                "prompt_file": filename,
                "prompt_version": version,
                "model": result.model,
                "fallback_used": result.fallback_used,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": str(result.cost_usd),
                "latency_ms": result.latency_ms,
                "output": result.value.model_dump(mode="json"),
            },
        )
        return result.value, result.input_tokens + result.output_tokens

    def _audit(self, cycle: CycleInput, kind: str, payload: Mapping[str, Any]) -> None:
        self.audit.append(AuditEvent(kind, payload, cycle_id=cycle.cycle_id))

    def _remaining(self, started: float) -> float:
        remaining = self.budget.wall_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def _exhausted(self, started: float, calls: int, tokens: int, steps: int) -> bool:
        return (
            calls >= self.budget.max_model_calls
            or tokens >= self.budget.max_tokens
            or steps >= self.budget.max_steps
            or time.monotonic() - started >= self.budget.wall_seconds
        )

    def _no_trade(
        self,
        cycle: CycleInput,
        reason: str,
        calls: int,
        tokens: int,
        proposal: Proposal | None = None,
        guardian: GuardianDecision | None = None,
    ) -> CycleResult:
        try:
            self._audit(cycle, "agent_cycle_no_trade", {"reason": reason})
        except Exception:
            pass
        return CycleResult("NO_TRADE", reason, proposal, guardian, None, calls, tokens)


class EventDebouncer:
    """Reject duplicate event/instrument cycles inside the configured gap."""

    def __init__(self, min_cycle_gap_seconds: float = 1.0) -> None:
        if min_cycle_gap_seconds < 0:
            raise ValueError("minimum cycle gap cannot be negative")
        self.gap = min_cycle_gap_seconds
        self._last: dict[tuple[str, str], datetime] = {}

    def allow(self, instrument: str, event: str, at: datetime) -> bool:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("event timestamp must be timezone-aware")
        key = (instrument.strip().upper(), event)
        stamp = at.astimezone(UTC)
        previous = self._last.get(key)
        if previous is not None and (stamp - previous).total_seconds() < self.gap:
            return False
        self._last[key] = stamp
        return True
