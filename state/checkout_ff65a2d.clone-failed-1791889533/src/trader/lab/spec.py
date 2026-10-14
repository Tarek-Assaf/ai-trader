"""Strict declarative strategy specifications; never executable source code."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, ValidationError, model_validator

FiniteFloat = Annotated[StrictFloat, Field(allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[StrictFloat, Field(gt=0, allow_inf_nan=False)]
IndicatorName = Literal[
    "open",
    "high",
    "low",
    "close",
    "sma",
    "ema",
    "rsi",
    "atr",
    "adx",
    "session_vwap",
    "bollinger_upper",
    "bollinger_lower",
    "rolling_high",
    "rolling_low",
    "prior_day_high",
    "prior_day_low",
    "opening_range_high",
    "opening_range_low",
    "utc_minute",
    "ny_minute",
    "asia_range_high",
    "asia_range_low",
    "hammer_shape",
    "inverted_shape",
    "bull_engulfing",
    "bear_engulfing",
    "doji",
    "flat_range_width_atr",
    "pre_trend_atr",
]
Comparison = Literal["gt", "gte", "lt", "lte", "eq", "ne"]
SessionName = Literal["asia", "london", "new_york", "closed"]
RegimeName = Literal[
    "trending_up", "trending_down", "ranging", "high_volatility", "normal_volatility"
]


class StrategySpecError(ValueError):
    """A JSON strategy is malformed, unsafe, or exceeds configured limits."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ComplexityLimits(StrictModel):
    max_conditions_per_strategy: int = Field(gt=0)
    max_tunable_parameters: int = Field(ge=0)


class ParameterReference(StrictModel):
    parameter: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")


class IndicatorCondition(StrictModel):
    indicator: IndicatorName
    period: int | None = Field(default=None, ge=1, le=10_000)
    operator: Comparison
    threshold: FiniteFloat | ParameterReference
    # When set, compare the left indicator with this second indicator instead
    # of a fixed number.  This keeps strategy ideas declarative while allowing
    # EMA crossovers, bands, and prior-bar/channel breakouts.
    threshold_indicator: IndicatorName | None = None
    threshold_period: int | None = Field(default=None, ge=1, le=10_000)
    threshold_lookback: int = Field(default=0, ge=0, le=10_000)
    threshold_offset: FiniteFloat | ParameterReference = 0.0
    threshold_atr_multiple: FiniteFloat | ParameterReference = 0.0
    threshold_atr_period: int = Field(default=14, ge=1, le=10_000)
    # Adds this many widths of the overnight (Asia) range to the reference, so
    # a level "1.5 ranges above the Asia high" is declared as
    # threshold_indicator="asia_range_high", threshold_range_multiple=1.5.
    threshold_range_multiple: FiniteFloat | ParameterReference = 0.0

    @model_validator(mode="after")
    def validate_indicator_parameters(self) -> IndicatorCondition:
        needs_period = self.indicator in {
            "sma",
            "ema",
            "rsi",
            "atr",
            "adx",
            "bollinger_upper",
            "bollinger_lower",
            "rolling_high",
            "rolling_low",
            "flat_range_width_atr",
            "pre_trend_atr",
        }
        if needs_period != (self.period is not None):
            raise ValueError("indicator period must be supplied only for period-based indicators")
        reference_needs_period = self.threshold_indicator in {
            "sma",
            "ema",
            "rsi",
            "atr",
            "adx",
            "bollinger_upper",
            "bollinger_lower",
            "rolling_high",
            "rolling_low",
            "flat_range_width_atr",
            "pre_trend_atr",
        }
        if self.threshold_indicator is None:
            if self.threshold_lookback or self.threshold_period is not None:
                raise ValueError("threshold reference fields require threshold_indicator")
        elif reference_needs_period != (self.threshold_period is not None):
            raise ValueError("threshold indicator period is invalid")
        if self.threshold_range_multiple != 0.0 and self.threshold_indicator not in {
            "asia_range_high",
            "asia_range_low",
        }:
            raise ValueError("threshold_range_multiple needs an Asia range reference")
        return self


class TunableParameter(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    value: FiniteFloat
    minimum: FiniteFloat
    maximum: FiniteFloat

    @model_validator(mode="after")
    def validate_bounds(self) -> TunableParameter:
        if self.minimum > self.maximum:
            raise ValueError("parameter minimum must not exceed maximum")
        if not self.minimum <= self.value <= self.maximum:
            raise ValueError("parameter value must be within its bounds")
        return self


DistanceUnit = Literal["points", "atr", "asia_range"]


class StopExit(StrictModel):
    kind: Literal["stop"]
    distance: PositiveFiniteFloat
    unit: DistanceUnit = "points"


class TargetExit(StrictModel):
    kind: Literal["target"]
    distance: PositiveFiniteFloat
    unit: DistanceUnit = "points"


class TimeStopExit(StrictModel):
    kind: Literal["time_stop"]
    bars: int = Field(gt=0, le=1_000_000)


class TrailingStopExit(StrictModel):
    kind: Literal["trailing"]
    activation_distance: PositiveFiniteFloat
    distance: PositiveFiniteFloat
    unit: DistanceUnit = "points"


type ExitRule = Annotated[
    StopExit | TargetExit | TimeStopExit | TrailingStopExit,
    Field(discriminator="kind"),
]


class StrategySpec(StrictModel):
    schema_version: Literal[1]
    instrument: str = Field(pattern=r"^[A-Z0-9_]{1,32}$")
    direction: Literal["long", "short"]
    timeframe_minutes: Literal[1, 3, 5, 15, 60]
    entry_conditions: tuple[IndicatorCondition, ...] = Field(min_length=1)
    exit_rules: tuple[ExitRule, ...] = Field(min_length=1)
    tunable_parameters: tuple[TunableParameter, ...] = ()
    regime_filters: tuple[RegimeName, ...] = ()
    session_filters: tuple[SessionName, ...] = ()

    @model_validator(mode="after")
    def validate_references_and_safety(self) -> StrategySpec:
        names = [item.name for item in self.tunable_parameters]
        if len(names) != len(set(names)):
            raise ValueError("tunable parameter names must be unique")
        known = set(names)
        for condition in self.entry_conditions:
            references = (
                condition.threshold,
                condition.threshold_offset,
                condition.threshold_atr_multiple,
            )
            for reference in references:
                if isinstance(reference, ParameterReference) and reference.parameter not in known:
                    raise ValueError("condition references an unknown tunable parameter")
        if not any(rule.kind == "stop" for rule in self.exit_rules):
            raise ValueError("every strategy must define a stop exit")
        return self


def _configured_limits() -> ComplexityLimits:
    """Read the checked-in Strategy Lab limits without importing config writers."""
    from trader.core.config import load_config_file

    path = Path(__file__).resolve().parents[3] / "config" / "strategy_lab.yaml"
    try:
        config = load_config_file(path)
        limits = config.model_dump()["complexity_limits"]
        return ComplexityLimits(
            max_conditions_per_strategy=limits["max_conditions_per_strategy"],
            max_tunable_parameters=limits["max_tunable_parameters"],
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise StrategySpecError("could not load Strategy Lab complexity limits") from exc


def _freeze_json_arrays(value: object) -> object:
    """Convert decoded JSON arrays to tuples so strict models remain immutable."""
    if isinstance(value, list):
        return tuple(_freeze_json_arrays(item) for item in value)
    if isinstance(value, dict):
        return {key: _freeze_json_arrays(item) for key, item in value.items()}
    return value


def parse_strategy_spec(
    raw: str | bytes | Mapping[str, object],
    *,
    complexity_limits: ComplexityLimits | None = None,
) -> StrategySpec:
    """Parse bounded JSON using configured limits; input is never interpreted as code."""
    try:
        if isinstance(raw, bytes):
            encoded = raw
        elif isinstance(raw, str):
            encoded = raw.encode("utf-8")
        elif isinstance(raw, Mapping):
            encoded = json.dumps(raw, allow_nan=False, separators=(",", ":")).encode("utf-8")
        else:
            raise StrategySpecError("strategy input must be JSON text, bytes, or a mapping")
        if len(encoded) > 65_536:
            raise StrategySpecError("strategy JSON exceeds 65536 bytes")
        payload = json.loads(
            encoded.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
        if not isinstance(payload, dict):
            raise StrategySpecError("strategy JSON must be an object")
        spec = StrategySpec.model_validate(_freeze_json_arrays(payload))
        limits = complexity_limits or _configured_limits()
        if len(spec.entry_conditions) > limits.max_conditions_per_strategy:
            raise StrategySpecError("too many entry conditions for configured Strategy Lab limits")
        if len(spec.tunable_parameters) > limits.max_tunable_parameters:
            raise StrategySpecError(
                "too many tunable parameters for configured Strategy Lab limits"
            )
        return spec
    except StrategySpecError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        ValueError,
        ValidationError,
    ) as exc:
        raise StrategySpecError("invalid or unsafe strategy specification") from exc
