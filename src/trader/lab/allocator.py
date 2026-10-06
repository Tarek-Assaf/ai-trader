"""Risk-budget allocation across a bounded, diverse strategy set."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class StrategyAllocation:
    strategy_id: str
    weight: Decimal
    risk_budget: Decimal


def _distance(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return 1.0 if not union else len(left ^ right) / len(union)


def allocate(
    strategies: Sequence[Mapping[str, Any]],
    *,
    risk_budget: Decimal | float,
    max_concurrent: int = 3,
    min_diversity: float = 0.3,
) -> tuple[StrategyAllocation, ...]:
    """Select highest-scoring live strategies while enforcing diversity."""
    if max_concurrent < 1 or not 0 <= min_diversity <= 1:
        raise ValueError("allocator limits are invalid")
    ranked = sorted(strategies, key=lambda row: float(row.get("score", 0)), reverse=True)
    chosen: list[Mapping[str, Any]] = []
    for candidate in ranked:
        features = frozenset(str(item) for item in candidate.get("features", ()))
        if all(
            _distance(
                features,
                frozenset(str(value) for value in item.get("features", ())),
            )
            >= min_diversity
            for item in chosen
        ):
            chosen.append(candidate)
        if len(chosen) >= max_concurrent:
            break
    scores = [Decimal(str(max(0.0, float(item.get("score", 0))))) for item in chosen]
    total = sum(scores, Decimal(0))
    budget = Decimal(str(risk_budget))
    if total <= 0:
        return ()
    allocations: list[StrategyAllocation] = []
    remaining = budget
    for index, (item, score) in enumerate(zip(chosen, scores, strict=True)):
        last = index == len(chosen) - 1
        share = remaining if last else (budget * score / total).quantize(Decimal("1e-12"))
        remaining -= share
        allocations.append(StrategyAllocation(str(item["strategy_id"]), score / total, share))
    return tuple(allocations)


def allocate_from_config(
    strategies: Sequence[Mapping[str, Any]],
    *,
    risk_budget: Decimal | float,
    config: Mapping[str, object],
) -> tuple[StrategyAllocation, ...]:
    portfolio = config.get("portfolio")
    if not isinstance(portfolio, Mapping):
        raise ValueError("portfolio config is missing")
    return allocate(
        strategies,
        risk_budget=risk_budget,
        max_concurrent=int(portfolio["max_concurrent_live_strategies"]),
        min_diversity=float(portfolio["min_strategy_diversity"]),
    )


__all__ = ["StrategyAllocation", "allocate", "allocate_from_config"]
