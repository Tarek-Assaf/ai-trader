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
                indicator="close", operator="lt", threshold=ParameterReference(parameter="pullback")
            ),
            IndicatorCondition(
                indicator="ema",
                period=21,
                operator="gt",
                threshold=ParameterReference(parameter="trend"),
            ),
        ),
        (
            TunableParameter(name="pullback", value=100.0, minimum=0.01, maximum=100000.0),
            TunableParameter(name="trend", value=0.0, minimum=-100000.0, maximum=100000.0),
        ),
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
                threshold=ParameterReference(parameter="level"),
            ),
            IndicatorCondition(
                indicator="ema",
                period=21,
                operator="gt",
                threshold=ParameterReference(parameter="level"),
            ),
        ),
        (TunableParameter(name="level", value=0.0, minimum=-100000.0, maximum=100000.0),),
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
                operator="lt",
                threshold=ParameterReference(parameter="rsi_low"),
            ),
        ),
        (TunableParameter(name="rsi_low", value=30.0, minimum=10.0, maximum=45.0),),
        target=1.2,
    )


def _bollinger_reversion(instrument: str, timeframe: int) -> StrategySpec:
    return _spec(
        instrument,
        timeframe,
        (
            IndicatorCondition(
                indicator="close", operator="lt", threshold=ParameterReference(parameter="lower")
            ),
            IndicatorCondition(
                indicator="sma",
                period=20,
                operator="gt",
                threshold=ParameterReference(parameter="lower"),
            ),
        ),
        (TunableParameter(name="lower", value=100.0, minimum=0.01, maximum=100000.0),),
        target=1.1,
    )


def _donchian_breakout(instrument: str, timeframe: int) -> StrategySpec:
    return _spec(
        instrument,
        timeframe,
        (
            IndicatorCondition(
                indicator="close", operator="gt", threshold=ParameterReference(parameter="breakout")
            ),
        ),
        (TunableParameter(name="breakout", value=0.0, minimum=-100000.0, maximum=100000.0),),
        stop=2.5,
        target=3.0,
    )


def _vwap_reversion(instrument: str, timeframe: int) -> StrategySpec:
    return _spec(
        instrument,
        timeframe,
        (
            IndicatorCondition(
                indicator="close", operator="lt", threshold=ParameterReference(parameter="vwap")
            ),
            IndicatorCondition(
                indicator="session_vwap",
                operator="gt",
                threshold=ParameterReference(parameter="vwap"),
            ),
        ),
        (TunableParameter(name="vwap", value=100.0, minimum=0.01, maximum=100000.0),),
        target=1.0,
    )


def _inside_bar(instrument: str, timeframe: int) -> StrategySpec:
    return _spec(
        instrument,
        timeframe,
        (
            IndicatorCondition(
                indicator="close", operator="gt", threshold=ParameterReference(parameter="trigger")
            ),
        ),
        (TunableParameter(name="trigger", value=0.0, minimum=-100000.0, maximum=100000.0),),
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
