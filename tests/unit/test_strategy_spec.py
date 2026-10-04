"""Security and behavior tests for the declarative Strategy Lab sandbox."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta

import pytest

from trader.data.resample import Candle
from trader.lab.engine import evaluate_entries
from trader.lab.spec import ComplexityLimits, StrategySpecError, parse_strategy_spec

LIMITS = ComplexityLimits(max_conditions_per_strategy=5, max_tunable_parameters=4)


def _spec(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "instrument": "NAS100",
        "timeframe_minutes": 1,
        "entry_conditions": [
            {"indicator": "rsi", "period": 2, "operator": "lt", "threshold": 40.0}
        ],
        "exit_rules": [{"kind": "stop", "distance": 10.0}],
        "tunable_parameters": [],
        "regime_filters": [],
        "session_filters": [],
    }
    value.update(overrides)
    return value


def _bars(count: int = 40) -> list[Candle]:
    start = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    values = [100.0 + (index % 5) if index % 2 else 100.0 - (index % 7) for index in range(count)]
    return [
        Candle(
            ts_utc=start + timedelta(minutes=index),
            open=price,
            high=price + 1.0,
            low=price - 1.0,
            close=price,
            volume=1.0,
            series_key=("NAS100", "bid"),
        )
        for index, price in enumerate(values)
    ]


def test_valid_spec_round_trips_to_json() -> None:
    spec = parse_strategy_spec(_spec(), complexity_limits=LIMITS)
    assert parse_strategy_spec(spec.model_dump_json(), complexity_limits=LIMITS) == spec


@pytest.mark.parametrize(
    "payload",
    [
        _spec(entry_conditions=[{"indicator": "unknown", "operator": "gt", "threshold": 1.0}]),
        _spec(
            entry_conditions=[{"indicator": "close.__class__", "operator": "gt", "threshold": 1.0}]
        ),
        _spec(
            entry_conditions=[
                {"indicator": "close", "operator": "gt", "threshold": "__import__('os')"}
            ]
        ),
        _spec(expression="__import__('os').system('id')"),
        _spec(exit_rules=[{"kind": "target", "distance": 2.0}]),
        _spec(
            tunable_parameters=[{"name": "floor", "value": 10.0, "minimum": 0.0, "maximum": 5.0}]
        ),
    ],
)
def test_invalid_or_executable_specs_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(StrategySpecError):
        parse_strategy_spec(payload, complexity_limits=LIMITS)


def test_condition_and_parameter_counts_follow_configured_limits() -> None:
    condition = {"indicator": "close", "operator": "gt", "threshold": 1.0}
    with pytest.raises(StrategySpecError, match="too many entry conditions"):
        parse_strategy_spec(_spec(entry_conditions=[condition] * 6), complexity_limits=LIMITS)
    params = [
        {"name": f"p{index}", "value": 1.0, "minimum": 0.0, "maximum": 2.0} for index in range(5)
    ]
    with pytest.raises(StrategySpecError, match="too many tunable parameters"):
        parse_strategy_spec(_spec(tunable_parameters=params), complexity_limits=LIMITS)


def test_parameter_references_are_resolved_only_by_declared_names() -> None:
    payload = _spec(
        entry_conditions=[
            {"indicator": "close", "operator": "gt", "threshold": {"parameter": "floor"}}
        ],
        tunable_parameters=[{"name": "floor", "value": 50.0, "minimum": 40.0, "maximum": 60.0}],
    )
    assert len(parse_strategy_spec(payload, complexity_limits=LIMITS).tunable_parameters) == 1
    payload["entry_conditions"] = [
        {"indicator": "close", "operator": "gt", "threshold": {"parameter": "missing"}}
    ]
    with pytest.raises(StrategySpecError):
        parse_strategy_spec(payload, complexity_limits=LIMITS)


def test_oversized_and_non_object_json_are_rejected() -> None:
    with pytest.raises(StrategySpecError, match="exceeds"):
        parse_strategy_spec(" " * 65_537, complexity_limits=LIMITS)
    with pytest.raises(StrategySpecError):
        parse_strategy_spec("[]", complexity_limits=LIMITS)


def test_random_json_never_leaks_non_validation_exceptions() -> None:
    rng = random.Random(0)
    scalars: list[object] = [None, True, False, 0, 1.5, "random", "__import__('os')"]

    def value(depth: int = 0) -> object:
        if depth >= 4 or rng.random() < 0.5:
            return rng.choice(scalars)
        if rng.random() < 0.5:
            return [value(depth + 1) for _ in range(rng.randrange(5))]
        return {
            f"key_{rng.randrange(10)}": value(depth + 1)
            for _ in range(rng.randrange(5))
        }

    for _ in range(250):
        encoded = json.dumps(value(), allow_nan=False)
        try:
            parse_strategy_spec(encoded, complexity_limits=LIMITS)
        except StrategySpecError:
            pass


def test_engine_emits_deterministic_signals_from_closed_bars() -> None:
    spec = parse_strategy_spec(_spec(), complexity_limits=LIMITS)
    candles = _bars()
    first = evaluate_entries(spec, candles)
    assert first == evaluate_entries(spec, candles)
    assert first
    assert all(signal.instrument == "NAS100" for signal in first)
    assert all(signal.timestamp_utc.tzinfo is UTC for signal in first)
    assert all(signal.timestamp_utc <= candles[-1].ts_utc for signal in first)


def test_engine_resamples_only_complete_bars_and_rejects_mixed_series() -> None:
    spec = parse_strategy_spec(_spec(timeframe_minutes=3), complexity_limits=LIMITS)
    candles = _bars(8)
    signals = evaluate_entries(spec, candles)
    assert all(signal.timestamp_utc.minute % 3 == 0 for signal in signals)
    assert all(signal.timestamp_utc < candles[-1].ts_utc for signal in signals)
    mixed = candles + [
        Candle(
            ts_utc=candles[0].ts_utc,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1.0,
            series_key=("XAUUSD", "bid"),
        )
    ]
    with pytest.raises(ValueError, match="one candle series"):
        evaluate_entries(spec, mixed)
