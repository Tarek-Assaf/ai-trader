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
    TimeStopExit,
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
            StopExit(kind="stop", distance=stop, unit="atr"),
            TargetExit(kind="target", distance=target, unit="atr"),
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


# --- Slow trend seeds -------------------------------------------------------------
# The first seed library traded 13-55 times a day for a gross edge of about one
# point, while a trade costs 4.0 points (NSXUSD) or 1.6 (XAUUSD).  These seeds trade
# rarely (long channel breakouts in the direction of a slow trend), use wide ATR exits
# so that cost is a small share of the target, and hold for hours.  They are plain
# declarative data like every other seed; gates and costs are unchanged.

SLOW_CHANNELS = (48, 96, 288)  # bars: 4h, 8h and 24h on the 5m chart
SLOW_EXITS = ((2.0, 4.0), (3.0, 6.0))  # (stop, target) in ATR multiples
SLOW_HOLD_BARS = 288


def _slow_trend_breakout(
    instrument: str,
    timeframe: int,
    *,
    channel: int,
    stop: float,
    target: float,
    direction: str,
    sessions: tuple[str, ...],
) -> StrategySpec:
    long_side = direction == "long"
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        timeframe_minutes=timeframe,  # type: ignore[arg-type]
        entry_conditions=(
            IndicatorCondition(
                indicator="close",
                operator="gt" if long_side else "lt",
                threshold=0.0,
                threshold_indicator="rolling_high" if long_side else "rolling_low",
                threshold_period=channel,
                threshold_lookback=1,
            ),
            IndicatorCondition(
                indicator="ema",
                period=50,
                operator="gt" if long_side else "lt",
                threshold=0.0,
                threshold_indicator="ema",
                threshold_period=200,
            ),
        ),
        exit_rules=(
            StopExit(kind="stop", distance=stop, unit="atr"),
            TargetExit(kind="target", distance=target, unit="atr"),
            TimeStopExit(kind="time_stop", bars=SLOW_HOLD_BARS),
        ),
        session_filters=sessions,  # type: ignore[arg-type]
    )


def slow_seed_specs(
    instrument: str, timeframe: int
) -> tuple[tuple[SeedStrategy, StrategySpec], ...]:
    """Return rarely-trading trend breakout seeds with wide ATR exits."""
    result: list[tuple[SeedStrategy, StrategySpec]] = []
    for channel in SLOW_CHANNELS:
        for stop, target in SLOW_EXITS:
            for direction in ("long", "short"):
                for label, sessions in (("all", ()), ("ny", ("new_york",))):
                    name = f"slow_trend_{direction}_{channel}_{stop:g}_{target:g}_{label}"
                    seed = SeedStrategy(name, "slow_trend", "slow_trend_breakout")
                    result.append(
                        (
                            seed,
                            _slow_trend_breakout(
                                instrument,
                                timeframe,
                                channel=channel,
                                stop=stop,
                                target=target,
                                direction=direction,
                                sessions=sessions,
                            ),
                        )
                    )
    return tuple(result)


SWING_CHANNELS = (96, 288)
SWING_ADX_MIN = 20.0
SWING_REVERSION_RSI = (25.0, 30.0)
SWING_REVERSION_EXITS = ((1.5, 3.0), (2.0, 4.0))
SWING_REVERSION_HOLD_BARS = 144


def _regime_trend_breakout(
    instrument: str,
    timeframe: int,
    *,
    channel: int,
    direction: str,
    sessions: tuple[str, ...],
) -> StrategySpec:
    """Slow channel breakout that only trades in a confirmed trending regime."""
    long_side = direction == "long"
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        timeframe_minutes=timeframe,  # type: ignore[arg-type]
        entry_conditions=(
            IndicatorCondition(
                indicator="close",
                operator="gt" if long_side else "lt",
                threshold=0.0,
                threshold_indicator="rolling_high" if long_side else "rolling_low",
                threshold_period=channel,
                threshold_lookback=1,
            ),
            IndicatorCondition(indicator="adx", period=14, operator="gt", threshold=SWING_ADX_MIN),
        ),
        exit_rules=(
            StopExit(kind="stop", distance=3.0, unit="atr"),
            TargetExit(kind="target", distance=6.0, unit="atr"),
            TimeStopExit(kind="time_stop", bars=SLOW_HOLD_BARS),
        ),
        regime_filters=("trending_up" if long_side else "trending_down",),
        session_filters=sessions,  # type: ignore[arg-type]
    )


def _ranging_reversion(
    instrument: str,
    timeframe: int,
    *,
    rsi_level: float,
    stop: float,
    target: float,
    direction: str,
    sessions: tuple[str, ...],
) -> StrategySpec:
    """Fade a stretched move below/above the Bollinger band, only in a ranging regime."""
    long_side = direction == "long"
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        timeframe_minutes=timeframe,  # type: ignore[arg-type]
        entry_conditions=(
            IndicatorCondition(
                indicator="rsi",
                period=14,
                operator="lt" if long_side else "gt",
                threshold=rsi_level if long_side else 100.0 - rsi_level,
            ),
            IndicatorCondition(
                indicator="close",
                operator="lt" if long_side else "gt",
                threshold=0.0,
                threshold_indicator="bollinger_lower" if long_side else "bollinger_upper",
                threshold_period=20,
            ),
        ),
        exit_rules=(
            StopExit(kind="stop", distance=stop, unit="atr"),
            TargetExit(kind="target", distance=target, unit="atr"),
            TimeStopExit(kind="time_stop", bars=SWING_REVERSION_HOLD_BARS),
        ),
        regime_filters=("ranging",),
        session_filters=sessions,  # type: ignore[arg-type]
    )


def swing_seed_specs(
    instrument: str, timeframe: int
) -> tuple[tuple[SeedStrategy, StrategySpec], ...]:
    """Return regime-filtered trend and ranging mean-reversion seeds with ATR exits."""
    result: list[tuple[SeedStrategy, StrategySpec]] = []
    for label, sessions in (("all", ()), ("ny", ("new_york",))):
        for direction in ("long", "short"):
            for channel in SWING_CHANNELS:
                name = f"regime_trend_{direction}_{channel}_{label}"
                seed = SeedStrategy(name, "regime_trend", "regime_trend_breakout")
                result.append(
                    (
                        seed,
                        _regime_trend_breakout(
                            instrument,
                            timeframe,
                            channel=channel,
                            direction=direction,
                            sessions=sessions,
                        ),
                    )
                )
            for rsi_level in SWING_REVERSION_RSI:
                for stop, target in SWING_REVERSION_EXITS:
                    name = f"range_revert_{direction}_{rsi_level:g}_{stop:g}_{target:g}_{label}"
                    seed = SeedStrategy(name, "range_reversion", "ranging_reversion")
                    result.append(
                        (
                            seed,
                            _ranging_reversion(
                                instrument,
                                timeframe,
                                rsi_level=rsi_level,
                                stop=stop,
                                target=target,
                                direction=direction,
                                sessions=sessions,
                            ),
                        )
                    )
    return tuple(result)


__all__ = [
    "SEED_LIBRARY",
    "SLOW_CHANNELS",
    "SeedStrategy",
    "seed_library",
    "seed_specs",
    "slow_seed_specs",
    "swing_seed_specs",
]
