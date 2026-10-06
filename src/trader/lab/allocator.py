"""Risk-budget allocation across a bounded, diverse strategy set."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class StrategyAllocation:
    strategy_id: str
    weight: Decimal
    risk_budget: Decimal


def _distance(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return 1.0 if not union else len(left ^ right) / len(union)


def allocate(
    strategies: Sequence[Mapping[str, object]],
    *,
    risk_budget: Decimal | float,
    max_concurrent: int = 3,
    min_diversity: float = 0.3,
) -> tuple[StrategyAllocation, ...]:
    """Select highest-scoring live strategies while enforcing diversity."""
    if max_concurrent < 1 or not 0 <= min_diversity <= 1:
        raise ValueError("allocator limits are invalid")
    ranked = sorted(strategies, key=lambda row: float(row.get("score", 0)), reverse=True)
    chosen: list[Mapping[str, object]] = []
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
    total = sum(max(0.0, float(item.get("score", 0))) for item in chosen)
    budget = Decimal(str(risk_budget))
    if total <= 0:
        return ()
    return tuple(
        StrategyAllocation(
            str(item["strategy_id"]),
            Decimal(str(max(0.0, float(item.get("score", 0))) / total)),
            budget * Decimal(str(max(0.0, float(item.get("score", 0))) / total)),
        )
        for item in chosen
    )


__all__ = ["StrategyAllocation", "allocate"]
