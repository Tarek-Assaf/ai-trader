import collections.abc
import decimal
import importlib

quality = importlib.import_module("trader.risk.quality")


WEIGHTS: dict[str, decimal.Decimal | float] = {
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
    stop_distance_atr: decimal.Decimal = decimal.Decimal("0.8"),
    spread_to_atr: decimal.Decimal = decimal.Decimal("0.5"),
    regime_fit: decimal.Decimal = decimal.Decimal("1"),
    timeframe_agreement: decimal.Decimal = decimal.Decimal("0.5"),
    event_proximity: decimal.Decimal = decimal.Decimal("1"),
    liquidity_session: decimal.Decimal = decimal.Decimal("0.75"),
    strategy_reliability: decimal.Decimal = decimal.Decimal("0.6"),
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


def _assert_value_error(operation: collections.abc.Callable[[], object]) -> None:
    try:
        operation()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_weighted_score_and_component_contributions_are_deterministic() -> None:
    result = quality.score_trade_quality(
        _components(),
        WEIGHTS,
        minimum_score=decimal.Decimal("0.70"),
    )

    assert result.score == decimal.Decimal("0.7050")
    assert result.eligible
    assert result.weighted_components == (
        ("stop_distance_atr", decimal.Decimal("0.120")),
        ("spread_to_atr", decimal.Decimal("0.100")),
        ("regime_fit", decimal.Decimal("0.15")),
        ("timeframe_agreement", decimal.Decimal("0.100")),
        ("event_proximity", decimal.Decimal("0.10")),
        ("liquidity_session", decimal.Decimal("0.0750")),
        ("strategy_reliability", decimal.Decimal("0.060")),
    )


def test_score_equal_to_threshold_is_eligible() -> None:
    features = _components(
        stop_distance_atr=decimal.Decimal("1"),
        spread_to_atr=decimal.Decimal("1"),
        regime_fit=decimal.Decimal("1"),
        timeframe_agreement=decimal.Decimal("1"),
        event_proximity=decimal.Decimal("1"),
        liquidity_session=decimal.Decimal("1"),
        strategy_reliability=decimal.Decimal("1"),
    )

    result = quality.score_trade_quality(
        features,
        WEIGHTS,
        minimum_score=decimal.Decimal("1"),
    )

    assert result.score == decimal.Decimal("1.00")
    assert result.eligible


def test_score_below_threshold_is_ineligible() -> None:
    result = quality.score_trade_quality(
        _components(regime_fit=decimal.Decimal("0")),
        WEIGHTS,
        minimum_score=decimal.Decimal("0.70"),
    )

    assert result.score == decimal.Decimal("0.5550")
    assert not result.eligible


def test_invalid_component_scores_fail_closed() -> None:
    for value in (
        decimal.Decimal("-0.01"),
        decimal.Decimal("1.01"),
        decimal.Decimal("NaN"),
        decimal.Decimal("Infinity"),
    ):
        _assert_value_error(
            lambda value=value: quality.score_trade_quality(
                _components(regime_fit=value),
                WEIGHTS,
                minimum_score=decimal.Decimal("0.50"),
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
            lambda weights=weights: quality.score_trade_quality(
                _components(),
                weights,
                minimum_score=decimal.Decimal("0.50"),
            )
        )


def test_invalid_threshold_fails_closed() -> None:
    for threshold in (decimal.Decimal("0"), decimal.Decimal("-0.1"), decimal.Decimal("1.01")):
        _assert_value_error(
            lambda threshold=threshold: quality.score_trade_quality(
                _components(),
                WEIGHTS,
                minimum_score=threshold,
            )
        )


def test_yaml_float_weights_are_converted_deterministically() -> None:
    result = quality.score_trade_quality(
        _components(),
        WEIGHTS,
        minimum_score=decimal.Decimal("0.705"),
    )

    assert result.score == decimal.Decimal("0.7050")
    assert result.eligible
