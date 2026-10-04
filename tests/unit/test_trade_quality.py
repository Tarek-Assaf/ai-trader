from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from trader.risk import quality


WEIGHTS: dict[str, Decimal | float] = {
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
) -> quality.TradeQualityComponents:
    return quality.TradeQualityComponents(
        stop_distance_atr=stop_distance_atr,
        spread_to_atr=spread_to_atr,
        regime_fit=regime_fit,
        timeframe_agreement=timeframe_agreement,
        event_proximity=event_proximity,
        liquidity_session=liquidity_session,
        strategy_reliability=strategy_reliability,
    )


def _assert_value_error(operation: Callable[[], object]) -> None:
    try:
        operation()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_weighted_score_and_component_contributions_are_deterministic() -> None:
    result = quality.score_trade_quality(
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

    result = quality.score_trade_quality(
        features,
        WEIGHTS,
        minimum_score=Decimal("1"),
    )

    assert result.score == Decimal("1.00")
    assert result.eligible


def test_score_below_threshold_is_ineligible() -> None:
    result = quality.score_trade_quality(
        _components(regime_fit=Decimal("0")),
        WEIGHTS,
        minimum_score=Decimal("0.70"),
    )

    assert result.score == Decimal("0.5550")
    assert not result.eligible


def test_invalid_component_scores_fail_closed() -> None:
    for value in (Decimal("-0.01"), Decimal("1.01"), Decimal("NaN"), Decimal("Infinity")):
        _assert_value_error(
            lambda: quality.score_trade_quality(
                _components(regime_fit=value),
                WEIGHTS,
                minimum_score=Decimal("0.50"),
            )
        )


def test_invalid_or_incomplete_weights_fail_closed() -> None:
    invalid_weights = (
        {key: value for key, value in WEIGHTS.items() if key != "regime_fit"},
        {**WEIGHTS, "unconfigured": 0.0},
        {**WEIGHTS, "regime_fit": -0.1},
        {**WEIGHTS, "regime_fit": float("nan")},
        {**WEIGHTS, "regime_fit": 0.1},
    )
    for weights in invalid_weights:
        _assert_value_error(
            lambda: quality.score_trade_quality(
                _components(),
                weights,
                minimum_score=Decimal("0.50"),
            )
        )


def test_invalid_threshold_fails_closed() -> None:
    for threshold in (Decimal("0"), Decimal("-0.1"), Decimal("1.01")):
        _assert_value_error(
            lambda: quality.score_trade_quality(
                _components(),
                WEIGHTS,
                minimum_score=threshold,
            )
        )


def test_yaml_float_weights_are_converted_deterministically() -> None:
    result = quality.score_trade_quality(
        _components(),
        WEIGHTS,
        minimum_score=Decimal("0.705"),
    )

    assert result.score == Decimal("0.7050")
    assert result.eligible
