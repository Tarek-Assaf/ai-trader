"""Deterministic weighted trade-quality score aggregation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

QUALITY_COMPONENTS = (
    "stop_distance_atr",
    "spread_to_atr",
    "regime_fit",
    "timeframe_agreement",
    "event_proximity",
    "liquidity_session",
    "strategy_reliability",
)


@dataclass(frozen=True, slots=True)
class TradeQualityComponents:
    """Trusted deterministic component scores, each normalized to [0, 1].

    Component producers are responsible for translating their underlying
    measurements into these bounded scores. This aggregate accepts no raw
    market text or LLM-generated values.
    """

    stop_distance_atr: Decimal
    spread_to_atr: Decimal
    regime_fit: Decimal
    timeframe_agreement: Decimal
    event_proximity: Decimal
    liquidity_session: Decimal
    strategy_reliability: Decimal


@dataclass(frozen=True, slots=True)
class TradeQualityResult:
    """Weighted score, threshold decision, and auditable contributions."""

    score: Decimal
    eligible: bool
    weighted_components: tuple[tuple[str, Decimal], ...]


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return result


def _component_scores(components: TradeQualityComponents) -> dict[str, Decimal]:
    scores = {
        name: _decimal(getattr(components, name), name)
        for name in QUALITY_COMPONENTS
    }
    if any(score < 0 or score > 1 for score in scores.values()):
        raise ValueError("trade-quality component scores must be in [0, 1]")
    return scores


def _validated_weights(weights: Mapping[str, object]) -> dict[str, Decimal]:
    supplied = set(weights)
    required = set(QUALITY_COMPONENTS)
    if supplied != required:
        missing = sorted(required - supplied)
        unexpected = sorted(supplied - required)
        raise ValueError(
            f"trade-quality weights must contain exactly the configured components "
            f"(missing={missing}, unexpected={unexpected})"
        )
    normalized = {name: _decimal(weights[name], f"weight {name}") for name in QUALITY_COMPONENTS}
    if any(weight < 0 for weight in normalized.values()):
        raise ValueError("trade-quality weights must be non-negative")
    if sum(normalized.values(), Decimal(0)) != Decimal(1):
        raise ValueError("trade-quality weights must sum to 1")
    return normalized


def score_trade_quality(
    components: TradeQualityComponents,
    weights: Mapping[str, object],
    *,
    minimum_score: Decimal,
) -> TradeQualityResult:
    """Combine normalized trusted feature scores and apply the configured gate.

    Invalid or incomplete feature scores and invalid weights raise ValueError.
    Callers must fail closed when they cannot provide a complete score.
    """
    threshold = _decimal(minimum_score, "minimum_score")
    if threshold <= 0 or threshold > 1:
        raise ValueError("minimum_score must be in (0, 1]")

    scores = _component_scores(components)
    normalized_weights = _validated_weights(weights)
    contributions = tuple(
        (name, scores[name] * normalized_weights[name]) for name in QUALITY_COMPONENTS
    )
    score = sum((value for _, value in contributions), Decimal(0))
    return TradeQualityResult(
        score=score,
        eligible=score >= threshold,
        weighted_components=contributions,
    )
