from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from trader.risk.quality import (
    QUALITY_COMPONENTS,
    TradeQualityComponents,
    score_trade_quality,
)


WEIGHTS: dict[str, Any] = {
    "stop_distance_atr": 0.15,
    "spread_to_atr": 0.20,
    "regime_fit": 0.15,
    "timeframe_agreement": 0.20,
    "event_proximity": 0.10,
    "liquidity_session": 0.10,
    "strategy_reliability": 0.10,
}


def _components(
    *,
    stop_distance_atr: Decimal = Decimal("0.8"),
    spread_to_atr: Decimal = Decimal("0.5"),
    regime_fit: Decimal = Decimal("1"),
    timeframe_agreement: Decimal = Decimal("0.5"),
    event_proximity: Decimal = Decimal("1"),
    liquidity_session: Decimal = Decimal("0.75"),
    strategy_reliability: Decimal = Decimal("0.6"),
) -> TradeQualityComponents:
    return TradeQualityComponents(
        stop_distance_atr=stop_distance_atr,
        spread_to_atr=spread_to_atr,
        regime_fit=regime_fit,
        timeframe_agreement=timeframe_agreement,
        event_proximity=event_proximity,
        liquidity_session=liquidity_session,
        strategy_reliability=strategy_reliability,
    )


def test_weighted_score_and_component_contributions_are_deterministic() -> None:
    result = score_trade_quality(
        _components(),
        WEIGHTS,
        minimum_score=Decimal("0.70"),
    )

    assert result.score == Decimal("0.7050")
    assert result.eligible
    assert result.weighted_components == (
        ("stop_distance_atr", Decimal("0.120")),
        ("spread_to_atr", Decimal("0.100")),
        ("regime_fit", Decimal("0.15")),
        ("timeframe_agreement", Decimal("0.100")),
        ("event_proximity", Decimal("0.10")),
        ("liquidity_session", Decimal("0.0750")),
        ("strategy_reliability", Decimal("0.060")),
    )


def test_score_equal_to_threshold_is_eligible() -> None:
    features = _components(
        stop_distance_atr=Decimal("1"),
        spread_to_atr=Decimal("1"),
        regime_fit=Decimal("1"),
        timeframe_agreement=Decimal("1"),
        event_proximity=Decimal("1"),
        liquidity_session=Decimal("1"),
        strategy_reliability=Decimal("1"),
    )

    result = score_trade_quality(
        features,
        WEIGHTS,
        minimum_score=Decimal("1"),
    )

    assert result.score == Decimal("1.00")
    assert result.eligible


def test_score_below_threshold_is_ineligible() -> None:
    result = score_trade_quality(
        _components(regime_fit=Decimal("0")),
        WEIGHTS,
        minimum_score=Decimal("0.70"),
    )

    assert result.score == Decimal("0.5550")
    assert not result.eligible


@pytest.mark.parametrize(
    "value",
    [
        Decimal("-0.01"),
        Decimal("1.01"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_invalid_component_scores_fail_closed(value: Decimal) -> None:
    with pytest.raises(ValueError):
        score_trade_quality(
            _components(regime_fit=value),
            WEIGHTS,
            minimum_score=Decimal("0.50"),
        )


@pytest.mark.parametrize(
    "weights",
    [
        {key: value for key, value in WEIGHTS.items() if key != "regime_fit"},
        {**WEIGHTS, "unconfigured": 0.0},
        {**WEIGHTS, "regime_fit": -0.1},
        {**WEIGHTS, "regime_fit": float("nan")},
        {**WEIGHTS, "regime_fit": 0.1},
    ],
)
def test_invalid_or_incomplete_weights_fail_closed(weights: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        score_trade_quality(
            _components(),
            weights,
            minimum_score=Decimal("0.50"),
        )


@pytest.mark.parametrize("threshold", [Decimal("0"), Decimal("-0.1"), Decimal("1.01")])
def test_invalid_threshold_fails_closed(threshold: Decimal) -> None:
    with pytest.raises(ValueError, match="minimum_score"):
        score_trade_quality(_components(), WEIGHTS, minimum_score=threshold)


def test_weights_may_come_from_yaml_float_values() -> None:
    assert set(WEIGHTS) == set(QUALITY_COMPONENTS)
    result = score_trade_quality(
        _components(),
        WEIGHTS,
        minimum_score=Decimal("0.705"),
    )

    assert result.score == Decimal("0.7050")
    assert result.eligible
