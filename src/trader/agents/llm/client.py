"""Typed provider routing, spend caps, fallback and health circuit breakers."""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import RLock
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine

from trader.agents.llm.ledger import SqlUsageLedger
from trader.agents.llm.types import (
    InvalidModelOutput,
    LLMError,
    Message,
    ModelUnavailable,
    Provider,
    TypedResult,
    UsageLedger,
)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class Tier:
    name: str
    provider: str
    model: str
    max_input_tokens: int
    max_output_tokens: int
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal

    def maximum_cost(self) -> Decimal:
        return (
            Decimal(self.max_input_tokens) * self.input_usd_per_million
            + Decimal(self.max_output_tokens) * self.output_usd_per_million
        ) / Decimal(1_000_000)

    def actual_cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (
            Decimal(input_tokens) * self.input_usd_per_million
            + Decimal(output_tokens) * self.output_usd_per_million
        ) / Decimal(1_000_000)


@dataclass(slots=True)
class _Circuit:
    failures: int = 0
    open_until: datetime | None = None


class LLMClient:
    """Route typed requests by role config while enforcing durable usage limits."""

    def __init__(
        self,
        *,
        config: Mapping[str, Any],
        providers: Mapping[str, Provider],
        ledger: UsageLedger,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.providers = dict(providers)
        self.ledger = ledger
        self._clock = clock
        self._monotonic = monotonic
        self._lock = RLock()
        try:
            self.roles = dict(config["roles"])
            self.fallback = dict(config["fallback"])
            health = config["health_probe"]
            self.probe_interval = timedelta(minutes=int(health["interval_minutes"]))
            self.probe_timeout_seconds = float(health["timeout_seconds"])
            self.failures_to_open = int(health["failures_to_open"])
            self.cooldown = timedelta(minutes=int(health["cooldown_minutes"]))
            self.tiers = {
                str(name): Tier(
                    name=str(name),
                    provider=str(value["provider"]),
                    model=str(value["model"]),
                    max_input_tokens=int(value["max_input_tokens"]),
                    max_output_tokens=int(value["max_output_tokens"]),
                    input_usd_per_million=Decimal(str(value["input_usd_per_million"])),
                    output_usd_per_million=Decimal(str(value["output_usd_per_million"])),
                )
                for name, value in config["tiers"].items()
            }
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise ValueError("invalid LLM configuration") from None
        self._validate_config()
        self._circuits = {name: _Circuit() for name in self.tiers}

    @classmethod
    def from_environment(
        cls,
        *,
        config: Mapping[str, Any],
        ledger: UsageLedger | None = None,
        engine: Engine | None = None,
        client: Any = None,
    ) -> LLMClient:
        """Build configured providers and a durable ledger, without requiring hosted keys."""
        from trader.agents.llm.providers import AnthropicProvider, OpenAICompatibleProvider

        if ledger is None:
            if engine is None:
                raise ValueError("an engine or usage ledger is required")
            try:
                budget = config["budget"]
                ledger = SqlUsageLedger(
                    engine,
                    daily_cap_usd=Decimal(str(budget["daily_usd_cap"])),
                    monthly_cap_usd=Decimal(str(budget["monthly_usd_cap"])),
                )
            except (KeyError, TypeError, ValueError, ArithmeticError):
                raise ValueError("invalid LLM spend budget configuration") from None

        providers: dict[str, Provider] = {}
        for name, provider_config in config.get("providers", {}).items():
            kind = provider_config.get("kind")
            key_name = provider_config.get("api_key_env", "")
            key = os.environ.get(key_name) if key_name else None
            base_url = str(provider_config.get("base_url", ""))
            if kind == "anthropic" and key:
                providers[name] = AnthropicProvider(api_key=key, base_url=base_url, client=client)
            elif kind == "openai_compatible" and (key or not key_name):
                providers[name] = OpenAICompatibleProvider(
                    base_url=base_url, api_key=key, client=client
                )
        return cls(config=config, providers=providers, ledger=ledger)

    def _validate_config(self) -> None:
        if not self.tiers or not self.roles:
            raise ValueError("at least one LLM tier and role are required")
        if (
            self.probe_interval.total_seconds() <= 0
            or self.probe_timeout_seconds <= 0
            or not math.isfinite(self.probe_timeout_seconds)
            or self.failures_to_open < 1
            or self.cooldown.total_seconds() <= 0
        ):
            raise ValueError("invalid LLM health probe configuration")
        for name, tier in self.tiers.items():
            if (
                not tier.provider
                or not tier.model
                or tier.max_input_tokens < 1
                or tier.max_output_tokens < 1
                or not tier.input_usd_per_million.is_finite()
                or not tier.output_usd_per_million.is_finite()
                or tier.input_usd_per_million <= 0
                or tier.output_usd_per_million <= 0
            ):
                raise ValueError(f"invalid model tier: {name}")
        for role, tier in self.roles.items():
            if not role or tier not in self.tiers:
                raise ValueError("each role must reference a configured tier")
        for name, fallback in self.fallback.items():
            if name not in self.tiers or fallback not in self.tiers or name == fallback:
                raise ValueError("fallbacks must reference a different configured tier")

    def _select_tier(self, requested: str, excluded: set[str] | None = None) -> tuple[Tier, bool]:
        now = self._clock()
        visited: set[str] = set()
        excluded = excluded or set()
        name: str | None = requested
        fallback_used = False
        with self._lock:
            while name is not None and name not in visited:
                visited.add(name)
                tier = self.tiers[name]
                circuit = self._circuits[name]
                is_open = circuit.open_until is not None and now < circuit.open_until
                if name not in excluded and not is_open and tier.provider in self.providers:
                    return tier, fallback_used
                name = self.fallback.get(name)
                fallback_used = True
        raise ModelUnavailable("configured model and its fallbacks are unavailable")

    def _record_health(self, tier_name: str, succeeded: bool) -> None:
        now = self._clock()
        with self._lock:
            circuit = self._circuits[tier_name]
            if succeeded:
                circuit.failures = 0
                circuit.open_until = None
            else:
                circuit.failures += 1
                if circuit.failures >= self.failures_to_open:
                    circuit.open_until = now + self.cooldown

    async def complete(
        self, role: str, messages: tuple[Message, ...], schema: type[T]
    ) -> TypedResult[T]:
        """Complete a role-configured call and route provider failures to its fallback."""
        if (
            role not in self.roles
            or not messages
            or any(
                item.role not in {"system", "user", "assistant"}
                or not isinstance(item.content, str)
                for item in messages
            )
            or not any(item.role in {"user", "assistant"} for item in messages)
        ):
            raise ValueError("known role and at least one message are required")
        requested = self.roles[role]
        excluded: set[str] = set()
        last_invalid = False
        for _attempt in range(2):
            tier, fallback_used = self._select_tier(requested, excluded)
            provider = self.providers[tier.provider]
            encoded = json.dumps(
                [{"role": item.role, "content": item.content} for item in messages],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > tier.max_input_tokens:
                raise LLMError("request exceeds the configured input budget")
            reserve = tier.maximum_cost()
            reservation_id = self.ledger.reserve(role=role, model=tier.model, maximum_cost=reserve)
            started = self._monotonic()
            try:
                reply = await provider.complete(
                    model=tier.model,
                    messages=messages,
                    schema=schema,
                    max_output_tokens=tier.max_output_tokens,
                )
            except LLMError:
                elapsed = max(0, int((self._monotonic() - started) * 1000))
                self.ledger.settle(
                    reservation_id,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=Decimal(0),
                    latency_ms=elapsed,
                    outcome="error",
                )
                self._record_health(tier.name, False)
                excluded.add(tier.name)
                last_invalid = False
                continue
            if (
                isinstance(reply.input_tokens, bool)
                or not isinstance(reply.input_tokens, int)
                or reply.input_tokens < 0
                or isinstance(reply.output_tokens, bool)
                or not isinstance(reply.output_tokens, int)
                or reply.output_tokens < 0
                or reply.input_tokens > tier.max_input_tokens
                or reply.output_tokens > tier.max_output_tokens
            ):
                self.ledger.settle(
                    reservation_id,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=Decimal(0),
                    latency_ms=max(0, int((self._monotonic() - started) * 1000)),
                    outcome="error",
                )
                self._record_health(tier.name, False)
                excluded.add(tier.name)
                last_invalid = False
                continue
            cost = tier.actual_cost(reply.input_tokens, reply.output_tokens)
            try:
                value = schema.model_validate_json(reply.text)
            except ValidationError:
                self.ledger.settle(
                    reservation_id,
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    cost_usd=cost,
                    latency_ms=max(0, int((self._monotonic() - started) * 1000)),
                    outcome="invalid_output",
                )
                self._record_health(tier.name, False)
                excluded.add(tier.name)
                last_invalid = True
                continue
            latency = max(0, int((self._monotonic() - started) * 1000))
            self.ledger.settle(
                reservation_id,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cost_usd=cost,
                latency_ms=latency,
                outcome="success",
            )
            self._record_health(tier.name, True)
            return TypedResult(
                value=value,
                role=role,
                model=tier.model,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cost_usd=cost,
                latency_ms=latency,
                fallback_used=fallback_used,
            )
        if last_invalid:
            raise InvalidModelOutput("model output failed schema validation safely")
        raise ModelUnavailable("model and configured fallback failed safely") from None

    async def run_health_probes(self) -> dict[str, bool]:
        """Probe each configured tier once; caller schedules this at the configured interval."""

        class Probe(BaseModel):
            ok: bool

        results: dict[str, bool] = {}
        for name in self.tiers:
            try:
                result = await asyncio.wait_for(
                    self._complete_tier(
                        name,
                        (Message(role="user", content='Return {"ok":true}.'),),
                        Probe,
                    ),
                    timeout=self.probe_timeout_seconds,
                )
                healthy = result.value.ok is True
            except (LLMError, TimeoutError):
                healthy = False
            self._record_health(name, healthy)
            results[name] = healthy
        return results

    async def run_health_monitor(self, stop: asyncio.Event) -> None:
        """Run configured probes until cancelled; this explicit timer is the health schedule."""
        while not stop.is_set():
            await self.run_health_probes()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.probe_interval.total_seconds())
            except TimeoutError:
                continue

    async def _complete_tier(
        self, tier_name: str, messages: tuple[Message, ...], schema: type[T]
    ) -> TypedResult[T]:
        tier = self.tiers[tier_name]
        provider = self.providers.get(tier.provider)
        if provider is None:
            raise ModelUnavailable("probe provider is not configured")
        probe_input_limit = min(128, tier.max_input_tokens)
        probe_output_limit = min(16, tier.max_output_tokens)
        probe_tier = Tier(
            name=tier.name,
            provider=tier.provider,
            model=tier.model,
            max_input_tokens=probe_input_limit,
            max_output_tokens=probe_output_limit,
            input_usd_per_million=tier.input_usd_per_million,
            output_usd_per_million=tier.output_usd_per_million,
        )
        reservation_id = self.ledger.reserve(
            role="health_probe", model=tier.model, maximum_cost=probe_tier.maximum_cost()
        )
        started = self._monotonic()
        try:
            reply = await provider.complete(
                model=tier.model,
                messages=messages,
                schema=schema,
                max_output_tokens=probe_output_limit,
            )
        except LLMError:
            self.ledger.settle(
                reservation_id,
                input_tokens=0,
                output_tokens=0,
                cost_usd=Decimal(0),
                latency_ms=max(0, int((self._monotonic() - started) * 1000)),
                outcome="error",
            )
            raise ModelUnavailable("health probe failed safely") from None
        if (
            isinstance(reply.input_tokens, bool)
            or not isinstance(reply.input_tokens, int)
            or reply.input_tokens < 0
            or isinstance(reply.output_tokens, bool)
            or not isinstance(reply.output_tokens, int)
            or reply.output_tokens < 0
            or reply.input_tokens > probe_input_limit
            or reply.output_tokens > probe_output_limit
        ):
            self.ledger.settle(
                reservation_id,
                input_tokens=0,
                output_tokens=0,
                cost_usd=Decimal(0),
                latency_ms=max(0, int((self._monotonic() - started) * 1000)),
                outcome="error",
            )
            raise LLMError("probe token use exceeds configured limits")
        cost = tier.actual_cost(reply.input_tokens, reply.output_tokens)
        try:
            value = schema.model_validate_json(reply.text)
        except ValidationError:
            self.ledger.settle(
                reservation_id,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cost_usd=cost,
                latency_ms=max(0, int((self._monotonic() - started) * 1000)),
                outcome="invalid_output",
            )
            raise InvalidModelOutput("health probe response was invalid") from None
        latency = max(0, int((self._monotonic() - started) * 1000))
        self.ledger.settle(
            reservation_id,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            cost_usd=cost,
            latency_ms=latency,
            outcome="success",
        )
        return TypedResult(
            value,
            "health_probe",
            tier.model,
            reply.input_tokens,
            reply.output_tokens,
            cost,
            latency,
        )
