"""Small, declarative textbook seed library for Strategy Lab runs.

Seeds are deliberately boring data.  They are bounded, typed ``StrategySpec``
values and carry a source note so every generated attempt can be traced in the
experiment ledger.  The lab may tune their parameters, but never executes
source supplied by an idea provider.
"""

from __future__ import annotations

from dataclasses import dataclass

from trader.lab.spec import (
    IndicatorCondition,
    ParameterReference,
    StopExit,
    StrategySpec,
    TargetExit,
    TunableParameter,
)


@dataclass(frozen=True, slots=True)
class SeedStrategy:
    name: str
    source: str
    factory: str

    def build(self, instrument: str, timeframe: int) -> StrategySpec:
        return _FACTORIES[self.factory](instrument, timeframe)


def _spec(
    instrument: str,
    timeframe: int,
    conditions: tuple[IndicatorCondition, ...],
    parameters: tuple[TunableParameter, ...] = (),
    *,
    direction: str = "long",
    stop: float = 1.5,
    target: float = 1.0,
) -> StrategySpec:
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        timeframe_minutes=timeframe,  # type: ignore[arg-type]
        entry_conditions=conditions,
        exit_rules=(
            StopExit(kind="stop", distance=stop),
            TargetExit(kind="target", distance=target),
        ),
        tunable_parameters=parameters,
    )


def _ema_pullback(instrument: str, timeframe: int) -> StrategySpec:
    return _spec(
        instrument,
        timeframe,
        (
            IndicatorCondition(
                indicator="close",
                operator="lt",
                threshold=0.0,
                threshold_indicator="ema",
                threshold_period=21,
                threshold_atr_multiple=ParameterReference(parameter="pullback_atr"),
            ),
            IndicatorCondition(
                indicator="ema",
                period=9,
                operator="gt",
                threshold=0.0,
                threshold_indicator="ema",
                threshold_period=21,
            ),
        ),
        (TunableParameter(name="pullback_atr", value=0.5, minimum=0.25, maximum=2.0),),
    )


def _ema_crossover(instrument: str, timeframe: int) -> StrategySpec:
    return _spec(
        instrument,
        timeframe,
        (
            IndicatorCondition(
                indicator="ema",
                period=9,
                operator="gt",
                threshold=0.0,
                threshold_indicator="ema",
                threshold_period=21,
            ),
        ),
        stop=2.0,
        target=2.5,
    )


def _rsi_reversion(instrument: str, timeframe: int) -> StrategySpec:
    return _spec(
        instrument,
        timeframe,
        (
            IndicatorCondition(
                indicator="rsi",
                period=14,
                operator="gt",
                threshold=ParameterReference(parameter="rsi_low"),
            ),
        ),
        (TunableParameter(name="rsi_low", value=55.0, minimum=35.0, maximum=65.0),),
        direction="short",
        target=1.2,
    )


def _bollinger_reversion(instrument: str, timeframe: int) -> StrategySpec:
    return _spec(
        instrument,
        timeframe,
        (
            IndicatorCondition(
                indicator="close",
                operator="lt",
                threshold=0.0,
                threshold_indicator="bollinger_lower",
                threshold_period=20,
                threshold_offset=ParameterReference(parameter="band_atr"),
            ),
        ),
        (TunableParameter(name="band_atr", value=1.0, minimum=0.0, maximum=2.0),),
        target=1.1,
    )


def _donchian_breakout(instrument: str, timeframe: int) -> StrategySpec:
    return _spec(
        instrument,
        timeframe,
        (
            IndicatorCondition(
                indicator="close",
                operator="gt",
                threshold=0.0,
                threshold_indicator="rolling_high",
                threshold_period=5,
                threshold_lookback=1,
                threshold_offset=ParameterReference(parameter="channel_buffer"),
            ),
        ),
        (TunableParameter(name="channel_buffer", value=-0.5, minimum=-1.0, maximum=0.0),),
        stop=2.5,
        target=3.0,
    )


def _vwap_reversion(instrument: str, timeframe: int) -> StrategySpec:
    return _spec(
        instrument,
        timeframe,
        (
            IndicatorCondition(
                indicator="close",
                operator="gt",
                threshold=0.0,
                threshold_indicator="session_vwap",
                threshold_offset=ParameterReference(parameter="vwap_buffer"),
            ),
        ),
        (TunableParameter(name="vwap_buffer", value=-0.25, minimum=-1.0, maximum=1.0),),
        target=1.0,
    )


def _inside_bar(instrument: str, timeframe: int) -> StrategySpec:
    return _spec(
        instrument,
        timeframe,
        (
            IndicatorCondition(
                indicator="close",
                operator="gt",
                threshold=0.0,
                threshold_indicator="rolling_high",
                threshold_period=1,
                threshold_lookback=1,
                threshold_offset=ParameterReference(parameter="inside_buffer"),
            ),
        ),
        (TunableParameter(name="inside_buffer", value=-0.5, minimum=-1.0, maximum=0.0),),
        stop=1.8,
        target=2.0,
    )


_FACTORIES = {
    "ema_pullback": _ema_pullback,
    "ema_crossover_atr_stop": _ema_crossover,
    "rsi_mean_reversion": _rsi_reversion,
    "bollinger_reversion": _bollinger_reversion,
    "donchian_breakout": _donchian_breakout,
    "vwap_session_reversion": _vwap_reversion,
    "inside_bar_breakout": _inside_bar,
}

SEED_LIBRARY: tuple[SeedStrategy, ...] = tuple(
    SeedStrategy(name, "textbook", name) for name in _FACTORIES
)


def seed_specs(instrument: str, timeframe: int) -> tuple[tuple[SeedStrategy, StrategySpec], ...]:
    """Return all bounded textbook seeds for one instrument/timeframe."""
    return tuple((seed, seed.build(instrument, timeframe)) for seed in SEED_LIBRARY)


seed_library = seed_specs

__all__ = ["SEED_LIBRARY", "SeedStrategy", "seed_library", "seed_specs"]
