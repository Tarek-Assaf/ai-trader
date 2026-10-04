"""Provider-neutral model request and response contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ProviderReply:
    text: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class TypedResult[T]:
    value: T
    role: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int
    fallback_used: bool = False


class Provider(Protocol):
    async def complete(
        self,
        *,
        model: str,
        messages: tuple[Message, ...],
        schema: type[BaseModel],
        max_output_tokens: int,
    ) -> ProviderReply: ...


class LLMError(RuntimeError):
    """Safe model call error; never includes a request, response, or credential."""


class BudgetExceeded(LLMError):
    """The call cannot be reserved under the configured spend caps."""


class ModelUnavailable(LLMError):
    """No healthy configured model or fallback is available."""


class InvalidModelOutput(LLMError):
    """The provider response did not match the requested typed schema."""


class UsageLedger(Protocol):
    def reserve(self, *, role: str, model: str, maximum_cost: Decimal) -> str: ...

    def settle(
        self,
        reservation_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal,
        latency_ms: int,
        outcome: str,
    ) -> None: ...
