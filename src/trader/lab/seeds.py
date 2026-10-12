"""Small, declarative textbook seed library for Strategy Lab runs.

Seeds are deliberately boring data.  They are bounded, typed ``StrategySpec``
values and carry a source note so every generated attempt can be traced in the
experiment ledger.  The lab may tune their parameters, but never executes
source supplied by an idea provider.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from trader.lab.spec import (
    IndicatorCondition,
    IndicatorName,
    ParameterReference,
    SessionName,
    StopExit,
    StrategySpec,
    TargetExit,
    TimeStopExit,
    TunableParameter,
)


def _bars(bars_on_5m: int, timeframe: int) -> int:
    """Express a length given in 5-minute bars as bars of ``timeframe`` minutes.

    Identity on the 5m chart.  A 288-bar (24 hour) channel stays about 24 hours
    on every timeframe instead of becoming 12 days on the 1h chart.
    """
    return max(5, round(bars_on_5m * 5 / timeframe))


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
                threshold_period=_bars(channel, timeframe),
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
            TimeStopExit(kind="time_stop", bars=_bars(SLOW_HOLD_BARS, timeframe)),
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
                threshold_period=_bars(channel, timeframe),
                threshold_lookback=1,
            ),
            IndicatorCondition(indicator="adx", period=14, operator="gt", threshold=SWING_ADX_MIN),
        ),
        exit_rules=(
            StopExit(kind="stop", distance=3.0, unit="atr"),
            TargetExit(kind="target", distance=6.0, unit="atr"),
            TimeStopExit(kind="time_stop", bars=_bars(SLOW_HOLD_BARS, timeframe)),
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
            TimeStopExit(kind="time_stop", bars=_bars(SWING_REVERSION_HOLD_BARS, timeframe)),
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


LEVEL_EXITS = ((1.5, 3.0), (2.0, 4.0))  # (stop, target) in ATR multiples, 1:2
LEVEL_HOLD_BARS = 288  # 24 hours on the 5m chart
RETEST_TOUCH_BARS = 12  # the pullback must have touched the level within 1 hour
RETEST_BREAKOUT_BARS = 96  # and price must have cleared it clearly within 8 hours


def _trend_filter(long_side: bool) -> IndicatorCondition:
    return IndicatorCondition(
        indicator="ema",
        period=50,
        operator="gt" if long_side else "lt",
        threshold=0.0,
        threshold_indicator="ema",
        threshold_period=200,
    )


def _prior_day_breakout(
    instrument: str,
    timeframe: int,
    *,
    direction: str,
    with_trend: bool,
    stop: float,
    target: float,
    sessions: tuple[str, ...],
) -> StrategySpec:
    """Close beyond the previous day's high (long) or low (short)."""
    long_side = direction == "long"
    conditions: list[IndicatorCondition] = [
        IndicatorCondition(
            indicator="close",
            operator="gt" if long_side else "lt",
            threshold=0.0,
            threshold_indicator="prior_day_high" if long_side else "prior_day_low",
        )
    ]
    if with_trend:
        conditions.append(_trend_filter(long_side))
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        timeframe_minutes=timeframe,  # type: ignore[arg-type]
        entry_conditions=tuple(conditions),
        exit_rules=(
            StopExit(kind="stop", distance=stop, unit="atr"),
            TargetExit(kind="target", distance=target, unit="atr"),
            TimeStopExit(kind="time_stop", bars=_bars(LEVEL_HOLD_BARS, timeframe)),
        ),
        session_filters=sessions,  # type: ignore[arg-type]
    )


def _opening_range_breakout(
    instrument: str,
    timeframe: int,
    *,
    direction: str,
    with_trend: bool,
    stop: float,
    target: float,
) -> StrategySpec:
    """Close beyond the first-30-minute New York range; needs bars of 30m or less."""
    long_side = direction == "long"
    conditions: list[IndicatorCondition] = [
        IndicatorCondition(
            indicator="close",
            operator="gt" if long_side else "lt",
            threshold=0.0,
            threshold_indicator="opening_range_high" if long_side else "opening_range_low",
        )
    ]
    if with_trend:
        conditions.append(_trend_filter(long_side))
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        timeframe_minutes=timeframe,  # type: ignore[arg-type]
        entry_conditions=tuple(conditions),
        exit_rules=(
            StopExit(kind="stop", distance=stop, unit="atr"),
            TargetExit(kind="target", distance=target, unit="atr"),
            TimeStopExit(kind="time_stop", bars=_bars(78, timeframe)),  # 6.5 hours
        ),
        session_filters=("new_york",),
    )


def _prior_day_retest(
    instrument: str,
    timeframe: int,
    *,
    direction: str,
    stop: float,
    target: float,
    sessions: tuple[str, ...],
) -> StrategySpec:
    """Break the previous day's level, pull back to it, and hold it."""
    long_side = direction == "long"
    level: IndicatorName = "prior_day_high" if long_side else "prior_day_low"
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
                threshold_indicator=level,
            ),
            IndicatorCondition(
                indicator="rolling_low" if long_side else "rolling_high",
                period=_bars(RETEST_TOUCH_BARS, timeframe),
                operator="lte" if long_side else "gte",
                threshold=0.0,
                threshold_indicator=level,
                threshold_atr_multiple=0.25 if long_side else -0.25,
            ),
            IndicatorCondition(
                indicator="rolling_high" if long_side else "rolling_low",
                period=_bars(RETEST_BREAKOUT_BARS, timeframe),
                operator="gt" if long_side else "lt",
                threshold=0.0,
                threshold_indicator=level,
                threshold_atr_multiple=0.5 if long_side else -0.5,
            ),
        ),
        exit_rules=(
            StopExit(kind="stop", distance=stop, unit="atr"),
            TargetExit(kind="target", distance=target, unit="atr"),
            TimeStopExit(kind="time_stop", bars=_bars(LEVEL_HOLD_BARS, timeframe)),
        ),
        session_filters=sessions,  # type: ignore[arg-type]
    )


def level_seed_specs(
    instrument: str, timeframe: int
) -> tuple[tuple[SeedStrategy, StrategySpec], ...]:
    """Return previous-day, opening-range and break-and-retest seeds (1:2 reward)."""
    result: list[tuple[SeedStrategy, StrategySpec]] = []
    for direction in ("long", "short"):
        for stop, target in LEVEL_EXITS:
            for with_trend in (False, True):
                tag = "trend" if with_trend else "any"
                for label, sessions in (("all", ()), ("ny", ("new_york",))):
                    name = f"level_pdbreak_{direction}_{tag}_{label}_{stop:g}_{target:g}"
                    result.append(
                        (
                            SeedStrategy(name, "level_pdbreak", "prior_day_breakout"),
                            _prior_day_breakout(
                                instrument,
                                timeframe,
                                direction=direction,
                                with_trend=with_trend,
                                stop=stop,
                                target=target,
                                sessions=sessions,
                            ),
                        )
                    )
                name = f"level_orb_{direction}_{tag}_{stop:g}_{target:g}"
                result.append(
                    (
                        SeedStrategy(name, "level_orb", "opening_range_breakout"),
                        _opening_range_breakout(
                            instrument,
                            timeframe,
                            direction=direction,
                            with_trend=with_trend,
                            stop=stop,
                            target=target,
                        ),
                    )
                )
            for label, sessions in (("all", ()), ("ny", ("new_york",))):
                name = f"level_retest_{direction}_{label}_{stop:g}_{target:g}"
                result.append(
                    (
                        SeedStrategy(name, "level_retest", "prior_day_retest"),
                        _prior_day_retest(
                            instrument,
                            timeframe,
                            direction=direction,
                            stop=stop,
                            target=target,
                            sessions=sessions,
                        ),
                    )
                )
    return tuple(result)


# --- Instrument-specific families (round A, owner approved Q-046) ----------------
# NAS100 is a US index: it trends in the US session and drifts upward.  Gold is a
# macro/dollar-driven metal: it ranges in the Asian session and breaks out at the
# London open and on US data (about 12:30-13:30 UTC).  Each instrument therefore
# gets its own ideas instead of copies of the other's.  Session tags are UTC:
# asia 00-09, london 07-16, new_york 12-21.

NAS_PULLBACK_RSI = (35.0, 40.0)
NAS_EXITS = ((1.5, 3.0), (2.0, 4.0))
GOLD_FADE_EXITS = ((1.0, 1.5), (1.5, 2.25))
GOLD_BREAK_EXITS = ((1.5, 3.0), (2.0, 4.0))
INSTRUMENT_HOLD_BARS = 144  # 12 hours on the 5m chart


def _exits(stop: float, target: float, timeframe: int) -> tuple[StopExit, TargetExit, TimeStopExit]:
    return (
        StopExit(kind="stop", distance=stop, unit="atr"),
        TargetExit(kind="target", distance=target, unit="atr"),
        TimeStopExit(kind="time_stop", bars=_bars(INSTRUMENT_HOLD_BARS, timeframe)),
    )


def _trend_pullback(
    instrument: str,
    timeframe: int,
    *,
    direction: str,
    rsi_level: float,
    stop: float,
    target: float,
) -> StrategySpec:
    """Buy a dip (sell a rally) inside the 50/200 EMA trend during the US session."""
    long_side = direction == "long"
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        timeframe_minutes=timeframe,  # type: ignore[arg-type]
        entry_conditions=(
            _trend_filter(long_side),
            IndicatorCondition(
                indicator="rsi",
                period=14,
                operator="lt" if long_side else "gt",
                threshold=rsi_level if long_side else 100.0 - rsi_level,
            ),
        ),
        exit_rules=_exits(stop, target, timeframe),
        session_filters=("new_york",),
    )


def _momentum_break(
    instrument: str,
    timeframe: int,
    *,
    direction: str,
    channel: int,
    adx_min: float,
    stop: float,
    target: float,
    sessions: tuple[str, ...],
) -> StrategySpec:
    """Break of a recent channel with ADX confirming momentum, in chosen sessions."""
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
                threshold_period=_bars(channel, timeframe),
                threshold_lookback=1,
            ),
            IndicatorCondition(indicator="adx", period=14, operator="gt", threshold=adx_min),
        ),
        exit_rules=_exits(stop, target, timeframe),
        session_filters=sessions,  # type: ignore[arg-type]
    )


def _asia_range_fade(
    instrument: str,
    timeframe: int,
    *,
    direction: str,
    stop: float,
    target: float,
    ranging_only: bool,
) -> StrategySpec:
    """Fade a stretched move beyond the Bollinger band in the quiet Asian session."""
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
                threshold=30.0 if long_side else 70.0,
            ),
            IndicatorCondition(
                indicator="close",
                operator="lt" if long_side else "gt",
                threshold=0.0,
                threshold_indicator="bollinger_lower" if long_side else "bollinger_upper",
                threshold_period=20,
            ),
        ),
        exit_rules=_exits(stop, target, timeframe),
        regime_filters=("ranging",) if ranging_only else (),
        session_filters=("asia",),
    )


def _london_break(
    instrument: str,
    timeframe: int,
    *,
    direction: str,
    stop: float,
    target: float,
    with_trend: bool,
) -> StrategySpec:
    """Break of the overnight (Asian, about 7 hour) range once London trades."""
    long_side = direction == "long"
    conditions = [
        IndicatorCondition(
            indicator="close",
            operator="gt" if long_side else "lt",
            threshold=0.0,
            threshold_indicator="rolling_high" if long_side else "rolling_low",
            threshold_period=_bars(84, timeframe),
            threshold_lookback=1,
        )
    ]
    if with_trend:
        conditions.append(_trend_filter(long_side))
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        timeframe_minutes=timeframe,  # type: ignore[arg-type]
        entry_conditions=tuple(conditions),
        exit_rules=_exits(stop, target, timeframe),
        session_filters=("london",),
    )


def _nas_specs(instrument: str, timeframe: int) -> list[tuple[SeedStrategy, StrategySpec]]:
    result: list[tuple[SeedStrategy, StrategySpec]] = []
    for direction in ("long", "short"):
        for stop, target in NAS_EXITS:
            for rsi_level in NAS_PULLBACK_RSI:
                name = f"nas_pullback_{direction}_{rsi_level:g}_{stop:g}_{target:g}"
                result.append(
                    (
                        SeedStrategy(name, "nas_pullback", "trend_pullback"),
                        _trend_pullback(
                            instrument,
                            timeframe,
                            direction=direction,
                            rsi_level=rsi_level,
                            stop=stop,
                            target=target,
                        ),
                    )
                )
            for label, sessions in (("ny", ("new_york",)), ("all", ())):
                name = f"nas_momentum_{direction}_{label}_{stop:g}_{target:g}"
                result.append(
                    (
                        SeedStrategy(name, "nas_momentum", "momentum_break"),
                        _momentum_break(
                            instrument,
                            timeframe,
                            direction=direction,
                            channel=36,
                            adx_min=20.0,
                            stop=stop,
                            target=target,
                            sessions=sessions,
                        ),
                    )
                )
    return result


def _gold_specs(instrument: str, timeframe: int) -> list[tuple[SeedStrategy, StrategySpec]]:
    result: list[tuple[SeedStrategy, StrategySpec]] = []
    for direction in ("long", "short"):
        for stop, target in GOLD_FADE_EXITS:
            for ranging_only in (True, False):
                tag = "ranging" if ranging_only else "any"
                name = f"gold_asia_fade_{direction}_{tag}_{stop:g}_{target:g}"
                result.append(
                    (
                        SeedStrategy(name, "gold_asia_fade", "asia_range_fade"),
                        _asia_range_fade(
                            instrument,
                            timeframe,
                            direction=direction,
                            stop=stop,
                            target=target,
                            ranging_only=ranging_only,
                        ),
                    )
                )
        for stop, target in GOLD_BREAK_EXITS:
            for with_trend in (False, True):
                tag = "trend" if with_trend else "any"
                name = f"gold_london_break_{direction}_{tag}_{stop:g}_{target:g}"
                result.append(
                    (
                        SeedStrategy(name, "gold_london_break", "london_break"),
                        _london_break(
                            instrument,
                            timeframe,
                            direction=direction,
                            stop=stop,
                            target=target,
                            with_trend=with_trend,
                        ),
                    )
                )
            name = f"gold_us_momentum_{direction}_{stop:g}_{target:g}"
            result.append(
                (
                    SeedStrategy(name, "gold_us_momentum", "momentum_break"),
                    _momentum_break(
                        instrument,
                        timeframe,
                        direction=direction,
                        channel=36,
                        adx_min=25.0,
                        stop=stop,
                        target=target,
                        sessions=("new_york",),
                    ),
                )
            )
    return result


def instrument_seed_specs(
    instrument: str, timeframe: int
) -> tuple[tuple[SeedStrategy, StrategySpec], ...]:
    """Return ideas written for one instrument's own behaviour (none for others)."""
    if instrument.startswith("NSX"):
        return tuple(_nas_specs(instrument, timeframe))
    if instrument.startswith("XAU"):
        return tuple(_gold_specs(instrument, timeframe))
    return ()


# --- Round B (owner approved D-029): time-of-day ideas ----------------------------
# Gold reacts to scheduled US data (8:30 New York = 12:30 or 13:30 UTC).  NAS100 often
# breaks the range built before the US open.  Both use the UTC minute of the day.

ROUND_B_DATA_WINDOWS = ((750, 840), (810, 900))  # 12:30-14:00 and 13:30-15:00 UTC
ROUND_B_PREOPEN_WINDOW = (810, 930)  # 13:30-15:30 UTC
ROUND_B_EXITS = ((1.5, 3.0), (2.0, 4.0))


def _time_window(start_minute: int, end_minute: int) -> tuple[IndicatorCondition, ...]:
    return (
        IndicatorCondition(indicator="utc_minute", operator="gte", threshold=float(start_minute)),
        IndicatorCondition(indicator="utc_minute", operator="lt", threshold=float(end_minute)),
    )


def _windowed_break(
    instrument: str,
    timeframe: int,
    *,
    direction: str,
    window: tuple[int, int],
    lookback_5m_bars: int,
    stop: float,
    target: float,
    with_trend: bool,
) -> StrategySpec:
    """Break of a recent range inside a fixed UTC time window."""
    long_side = direction == "long"
    conditions = [
        *_time_window(*window),
        IndicatorCondition(
            indicator="close",
            operator="gt" if long_side else "lt",
            threshold=0.0,
            threshold_indicator="rolling_high" if long_side else "rolling_low",
            threshold_period=_bars(lookback_5m_bars, timeframe),
            threshold_lookback=1,
        ),
    ]
    if with_trend:
        conditions.append(_trend_filter(long_side))
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        timeframe_minutes=timeframe,  # type: ignore[arg-type]
        entry_conditions=tuple(conditions),
        exit_rules=(
            StopExit(kind="stop", distance=stop, unit="atr"),
            TargetExit(kind="target", distance=target, unit="atr"),
            TimeStopExit(kind="time_stop", bars=_bars(36, timeframe)),
        ),
    )


def round_b_seed_specs(
    instrument: str, timeframe: int
) -> tuple[tuple[SeedStrategy, StrategySpec], ...]:
    """Return the Round B time-of-day ideas for one instrument (none for others)."""
    result: list[tuple[SeedStrategy, StrategySpec]] = []
    for direction in ("long", "short"):
        for stop, target in ROUND_B_EXITS:
            if instrument.startswith("XAU"):
                for window in ROUND_B_DATA_WINDOWS:
                    name = f"gold_data_break_{direction}_{window[0]}_{stop:g}_{target:g}"
                    result.append(
                        (
                            SeedStrategy(name, "gold_data_break", "windowed_break"),
                            _windowed_break(
                                instrument,
                                timeframe,
                                direction=direction,
                                window=window,
                                lookback_5m_bars=12,
                                stop=stop,
                                target=target,
                                with_trend=False,
                            ),
                        )
                    )
            elif instrument.startswith("NSX"):
                for with_trend in (False, True):
                    tag = "trend" if with_trend else "any"
                    name = f"nas_preopen_break_{direction}_{tag}_{stop:g}_{target:g}"
                    result.append(
                        (
                            SeedStrategy(name, "nas_preopen_break", "windowed_break"),
                            _windowed_break(
                                instrument,
                                timeframe,
                                direction=direction,
                                window=ROUND_B_PREOPEN_WINDOW,
                                lookback_5m_bars=90,
                                stop=stop,
                                target=target,
                                with_trend=with_trend,
                            ),
                        )
                    )
    return tuple(result)


# --- Round C: overnight-range level ideas (OSTDV) and ICT concepts -----------
#
# OSTDV levels are multiples of the overnight (Asia) range, 18:45-22:03 New
# York time.  A level "k ranges beyond the Asia high" is the high plus k times
# the range width; the Fibonacci numbers on the owner's chart map to k as
# follows: 0 and 1 are the range edges, -0.5/1.5 -> k 0.5, -1/2 -> k 1,
# -1.5/2.5 -> k 1.5, -2/3 -> k 2 (micro SMR starts), -2.272/3.272 -> k 2.272,
# -3/4 -> k 3 (SMR).  The set is fixed up front to keep multiple testing small.
ROUND_C_EXITS = ROUND_B_EXITS
# Exits in Asia-range widths (the OSTDV ruler): stop one step (0.5) or 0.35 of a range beyond,
# target two steps (1.0) or 0.7 of a range back toward the range.
ROUND_C_RANGE_EXITS = ((0.5, 1.0), (0.35, 0.7))
OTE_EDGE = 0.62  # outer edge of the 0.5-0.62 support/resistance zone
OSTDV_SWEEP_DEPTHS = (0.0, 0.1111)
OSTDV_FADE_LEVELS = (0.5, 1.0, 1.5, 2.0, 2.272, 3.0)
OSTDV_BREAK_LEVELS = (0.5, 1.0)
OSTDV_RANGE_FADE_LEVELS = (0.5, 1.0, 1.5, 2.0)
# Windows in New York clock minutes: all day, London and New York kill zones.
ROUND_C_WINDOWS: tuple[tuple[str, tuple[int, int] | None], ...] = (
    ("any", None),
    ("london", (120, 300)),
    ("newyork", (420, 600)),
)
ICT_CANDLE_SHAPES = {
    "long": (
        ("hammer", "hammer_shape"),
        ("invhammer", "inverted_shape"),
        ("bullengulf", "bull_engulfing"),
    ),
    "short": (
        ("hangingman", "hammer_shape"),
        ("shootingstar", "inverted_shape"),
        ("bearengulf", "bear_engulfing"),
    ),
}


def _ny_window(window: tuple[int, int] | None) -> tuple[IndicatorCondition, ...]:
    if window is None:
        return ()
    return (
        IndicatorCondition(indicator="ny_minute", operator="gte", threshold=float(window[0])),
        IndicatorCondition(indicator="ny_minute", operator="lt", threshold=float(window[1])),
    )


def _asia_level(long_side: bool, depth: float) -> dict[str, object]:
    """Threshold fields for a level ``depth`` ranges beyond the Asia low (long) or high."""
    return {
        "threshold": 0.0,
        "threshold_indicator": "asia_range_low" if long_side else "asia_range_high",
        "threshold_range_multiple": -depth if long_side else depth,
    }


def _round_c_spec(
    instrument: str,
    timeframe: int,
    direction: str,
    conditions: Sequence[IndicatorCondition],
    stop: float,
    target: float,
    unit: str = "atr",
) -> StrategySpec:
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        timeframe_minutes=timeframe,  # type: ignore[arg-type]
        entry_conditions=tuple(conditions),
        exit_rules=(
            StopExit(kind="stop", distance=stop, unit=unit),  # type: ignore[arg-type]
            TargetExit(kind="target", distance=target, unit=unit),  # type: ignore[arg-type]
            TimeStopExit(kind="time_stop", bars=_bars(36, timeframe)),
        ),
    )


def round_c_seed_specs(
    instrument: str, timeframe: int
) -> tuple[tuple[SeedStrategy, StrategySpec], ...]:
    """Return the Round C ideas (same families for every instrument)."""
    result: list[tuple[SeedStrategy, StrategySpec]] = []

    def add(family: str, name: str, spec: StrategySpec) -> None:
        result.append((SeedStrategy(name, family, family), spec))

    for direction in ("long", "short"):
        long_side = direction == "long"
        poke = "low" if long_side else "high"
        for stop, target in ROUND_C_EXITS:
            tag = f"{direction}_{stop:g}_{target:g}"
            # 1. Sweep of the Asia edge, then a close back inside the range.
            for depth in OSTDV_SWEEP_DEPTHS:
                for window_name, window in ROUND_C_WINDOWS:
                    conditions = [
                        *_ny_window(window),
                        IndicatorCondition(
                            indicator=poke,  # type: ignore[arg-type]
                            operator="lt" if long_side else "gt",
                            **_asia_level(long_side, depth),  # type: ignore[arg-type]
                        ),
                        IndicatorCondition(
                            indicator="close",
                            operator="gt" if long_side else "lt",
                            **_asia_level(long_side, 0.0),  # type: ignore[arg-type]
                        ),
                    ]
                    add(
                        "ostdv_sweep",
                        f"ostdv_sweep_{depth:g}_{window_name}_{tag}",
                        _round_c_spec(instrument, timeframe, direction, conditions, stop, target),
                    )
            # 2. Fade a deeper level: poke through it, close back on the near side.
            for depth in OSTDV_FADE_LEVELS:
                conditions = [
                    IndicatorCondition(
                        indicator=poke,  # type: ignore[arg-type]
                        operator="lt" if long_side else "gt",
                        **_asia_level(long_side, depth),  # type: ignore[arg-type]
                    ),
                    IndicatorCondition(
                        indicator="close",
                        operator="gt" if long_side else "lt",
                        **_asia_level(long_side, depth),  # type: ignore[arg-type]
                    ),
                ]
                add(
                    "ostdv_fade",
                    f"ostdv_fade_{depth:g}_{tag}",
                    _round_c_spec(instrument, timeframe, direction, conditions, stop, target),
                )
            # 3. Break and go: a bar that opens inside and closes beyond the level.
            for depth in OSTDV_BREAK_LEVELS:
                # Long breaks above the Asia high + depth ranges (short mirrors it below).
                conditions = [
                    IndicatorCondition(
                        indicator="open",
                        operator="lte" if long_side else "gte",
                        **_asia_level(not long_side, depth),  # type: ignore[arg-type]
                    ),
                    IndicatorCondition(
                        indicator="close",
                        operator="gt" if long_side else "lt",
                        **_asia_level(not long_side, depth),  # type: ignore[arg-type]
                    ),
                ]
                add(
                    "ostdv_break",
                    f"ostdv_break_{depth:g}_{tag}",
                    _round_c_spec(instrument, timeframe, direction, conditions, stop, target),
                )
            # 4. ICT: sweep of liquidity, then a fair value gap, then a close back.
            sweep_bars = _bars(12, timeframe)
            for level_name in ("asia", "prior_day"):
                level_fields: dict[str, object]
                if level_name == "asia":
                    level_fields = _asia_level(long_side, 0.0)
                else:
                    level_fields = {
                        "threshold": 0.0,
                        "threshold_indicator": "prior_day_low" if long_side else "prior_day_high",
                    }
                for window_name, window in ROUND_C_WINDOWS:
                    conditions = [
                        *_ny_window(window),
                        IndicatorCondition(
                            indicator="rolling_low" if long_side else "rolling_high",
                            period=sweep_bars,
                            operator="lt" if long_side else "gt",
                            **level_fields,  # type: ignore[arg-type]
                        ),
                        # Fair value gap: this bar's low is above the high two bars ago.
                        IndicatorCondition(
                            indicator="low" if long_side else "high",
                            operator="gt" if long_side else "lt",
                            threshold=0.0,
                            threshold_indicator="high" if long_side else "low",
                            threshold_lookback=2,
                        ),
                        IndicatorCondition(
                            indicator="close",
                            operator="gt" if long_side else "lt",
                            **level_fields,  # type: ignore[arg-type]
                        ),
                    ]
                    add(
                        "ict_sweep_fvg",
                        f"ict_sweep_fvg_{level_name}_{window_name}_{tag}",
                        _round_c_spec(instrument, timeframe, direction, conditions, stop, target),
                    )
                # 5. Candle shapes at a swept level (hammer, engulfing, ...).
                for shape_name, shape in ICT_CANDLE_SHAPES[direction]:
                    for window_name, window in ROUND_C_WINDOWS:
                        conditions = [
                            *_ny_window(window),
                            IndicatorCondition(
                                indicator=shape,  # type: ignore[arg-type]
                                operator="eq",
                                threshold=1.0,
                            ),
                            IndicatorCondition(
                                indicator=poke,  # type: ignore[arg-type]
                                operator="lt" if long_side else "gt",
                                **level_fields,  # type: ignore[arg-type]
                            ),
                            IndicatorCondition(
                                indicator="close",
                                operator="gt" if long_side else "lt",
                                **level_fields,  # type: ignore[arg-type]
                            ),
                        ]
                        add(
                            "candle_at_level",
                            f"candle_{shape_name}_{level_name}_{window_name}_{tag}",
                            _round_c_spec(
                                instrument, timeframe, direction, conditions, stop, target
                            ),
                        )
            # 7. OTE zone (0.5-0.62 of the range, measured from the trade's own 0 edge)
            # acts as support from above (buy) and resistance from below (sell).
            for window_name, window in ROUND_C_WINDOWS:
                if long_side:  # 0 = Asia low: zone is low + 0.5..0.62 ranges, price above it
                    reference, edge = "asia_range_low", OTE_EDGE
                else:  # 0 = Asia high: zone is high - 0.62..0.5 ranges, price below it
                    reference, edge = "asia_range_high", -OTE_EDGE
                conditions = [
                    *_ny_window(window),
                    IndicatorCondition(
                        indicator="low" if long_side else "high",
                        operator="lt" if long_side else "gt",
                        threshold=0.0,
                        threshold_indicator=reference,  # type: ignore[arg-type]
                        threshold_range_multiple=edge,
                    ),
                    IndicatorCondition(
                        indicator="close",
                        operator="gt" if long_side else "lt",
                        threshold=0.0,
                        threshold_indicator=reference,  # type: ignore[arg-type]
                        threshold_range_multiple=edge,
                    ),
                ]
                add(
                    "ostdv_ote",
                    f"ostdv_ote_{window_name}_{tag}",
                    _round_c_spec(instrument, timeframe, direction, conditions, stop, target),
                )
            # 6. ICT structure shift after a sweep of the Asia edge (kill zones only).
            for window_name, window in ROUND_C_WINDOWS[1:]:
                conditions = [
                    *_ny_window(window),
                    IndicatorCondition(
                        indicator="rolling_low" if long_side else "rolling_high",
                        period=sweep_bars,
                        operator="lt" if long_side else "gt",
                        **_asia_level(long_side, 0.0),  # type: ignore[arg-type]
                    ),
                    IndicatorCondition(
                        indicator="close",
                        operator="gt" if long_side else "lt",
                        threshold=0.0,
                        threshold_indicator="rolling_high" if long_side else "rolling_low",
                        threshold_period=_bars(6, timeframe),
                        threshold_lookback=1,
                    ),
                ]
                add(
                    "ict_structure_shift",
                    f"ict_structure_shift_{window_name}_{tag}",
                    _round_c_spec(instrument, timeframe, direction, conditions, stop, target),
                )
    # Same entries as the sweep/fade/OTE ideas above, but with exits in Asia-range widths so
    # stops and targets sit on the OSTDV levels (stop one step beyond, target back to the range).
    for direction in ("long", "short"):
        long_side = direction == "long"
        poke = "low" if long_side else "high"
        for stop, target in ROUND_C_RANGE_EXITS:
            tag = f"{direction}_r{stop:g}_{target:g}"
            for depth in OSTDV_RANGE_FADE_LEVELS:
                conditions = [
                    IndicatorCondition(
                        indicator=poke,  # type: ignore[arg-type]
                        operator="lt" if long_side else "gt",
                        **_asia_level(long_side, depth),  # type: ignore[arg-type]
                    ),
                    IndicatorCondition(
                        indicator="close",
                        operator="gt" if long_side else "lt",
                        **_asia_level(long_side, depth),  # type: ignore[arg-type]
                    ),
                ]
                add(
                    "ostdv_fade_range_exit",
                    f"ostdv_fade_{depth:g}_{tag}",
                    _round_c_spec(
                        instrument, timeframe, direction, conditions, stop, target, "asia_range"
                    ),
                )
            for window_name, window in ROUND_C_WINDOWS:
                sweep = [
                    *_ny_window(window),
                    IndicatorCondition(
                        indicator=poke,  # type: ignore[arg-type]
                        operator="lt" if long_side else "gt",
                        **_asia_level(long_side, 0.1111),  # type: ignore[arg-type]
                    ),
                    IndicatorCondition(
                        indicator="close",
                        operator="gt" if long_side else "lt",
                        **_asia_level(long_side, 0.0),  # type: ignore[arg-type]
                    ),
                ]
                add(
                    "ostdv_sweep_range_exit",
                    f"ostdv_sweep_{window_name}_{tag}",
                    _round_c_spec(
                        instrument, timeframe, direction, sweep, stop, target, "asia_range"
                    ),
                )
                sweep_bars = _bars(12, timeframe)
                fvg = [
                    *_ny_window(window),
                    IndicatorCondition(
                        indicator="rolling_low" if long_side else "rolling_high",
                        period=sweep_bars,
                        operator="lt" if long_side else "gt",
                        **_asia_level(long_side, 0.0),  # type: ignore[arg-type]
                    ),
                    IndicatorCondition(
                        indicator="low" if long_side else "high",
                        operator="gt" if long_side else "lt",
                        threshold=0.0,
                        threshold_indicator="high" if long_side else "low",
                        threshold_lookback=2,
                    ),
                    IndicatorCondition(
                        indicator="close",
                        operator="gt" if long_side else "lt",
                        **_asia_level(long_side, 0.0),  # type: ignore[arg-type]
                    ),
                ]
                add(
                    "ict_sweep_fvg_range_exit",
                    f"ict_sweep_fvg_asia_{window_name}_{tag}",
                    _round_c_spec(
                        instrument, timeframe, direction, fvg, stop, target, "asia_range"
                    ),
                )
    return tuple(result)


__all__ = [
    "SEED_LIBRARY",
    "SLOW_CHANNELS",
    "SeedStrategy",
    "round_c_seed_specs",
    "seed_library",
    "instrument_seed_specs",
    "seed_specs",
    "level_seed_specs",
    "round_b_seed_specs",
    "slow_seed_specs",
    "swing_seed_specs",
]


# --- Round D: Wyckoff ideas (docs/specs/round_d_wyckoff.md) -----------------
#
# Declarative approximations of Wyckoff's accumulation / distribution events.  The
# declarative language has no event memory and at most five conditions per strategy, so:
# the trading range + preceding trend are checked by two range indicators; "spring within
# the last 12 bars" is expressed with rolling extremes; stops and targets are ATR multiples
# rather than "0.25 ATR beyond the spring extreme".  All deviations are listed in the spec.
WYCKOFF_RANGE_BARS = (48, 96, 192, 288)  # five-minute-equivalent bars
WYCKOFF_EXITS = ((1.0, 2.0),)  # one exit keeps trials near 360 per market/timeframe
WYCKOFF_SESSIONS: tuple[tuple[str, tuple[SessionName, ...]], ...] = (
    ("any", ()),
    ("london", ("london",)),
    ("newyork", ("new_york",)),
)
WYCKOFF_BREAK_ATR = 0.5
WYCKOFF_TEST_BARS = 12
WYCKOFF_SOS_BARS = 10
WYCKOFF_TREND_ATR = 3.0


def round_d_seed_specs(
    instrument: str, timeframe: int
) -> tuple[tuple[SeedStrategy, StrategySpec], ...]:
    """Return the Wyckoff spring/upthrust, test and sign-of-strength/weakness ideas."""
    result: list[tuple[SeedStrategy, StrategySpec]] = []
    for direction in ("long", "short"):
        long_side = direction == "long"
        sign = -1.0 if long_side else 1.0
        edge: IndicatorName = "rolling_low" if long_side else "rolling_high"
        poke: IndicatorName = "low" if long_side else "high"
        for range_n5 in WYCKOFF_RANGE_BARS:
            n = _bars(range_n5, timeframe)
            trend = (
                TunableParameter(
                    name="trend_atr",
                    value=sign * WYCKOFF_TREND_ATR,
                    minimum=min(sign * 4.0, sign * 2.0),
                    maximum=max(sign * 4.0, sign * 2.0),
                ),
            )
            context = (
                IndicatorCondition(
                    indicator="pre_trend_atr",
                    period=n,
                    operator="lte" if long_side else "gte",
                    threshold=ParameterReference(parameter="trend_atr"),
                ),
                IndicatorCondition(
                    indicator="flat_range_width_atr",
                    period=n,
                    operator="gte",
                    threshold=1.5,
                ),
            )
            window = max(5, _bars(WYCKOFF_TEST_BARS * 5, timeframe))
            recent = max(5, _bars(WYCKOFF_SOS_BARS * 5, timeframe))
            spring = (
                IndicatorCondition(
                    indicator=poke,
                    operator="lt" if long_side else "gt",
                    threshold=0.0,
                    threshold_indicator=edge,
                    threshold_period=n,
                    threshold_lookback=1,
                ),
                IndicatorCondition(
                    indicator=poke,
                    operator="gt" if long_side else "lt",
                    threshold=0.0,
                    threshold_indicator=edge,
                    threshold_period=n,
                    threshold_lookback=1,
                    threshold_atr_multiple=sign * WYCKOFF_BREAK_ATR,
                ),
                IndicatorCondition(
                    indicator="close",
                    operator="gt" if long_side else "lt",
                    threshold=0.0,
                    threshold_indicator=edge,
                    threshold_period=n,
                    threshold_lookback=1,
                ),
            )
            test = (
                IndicatorCondition(
                    indicator=edge,
                    period=window,
                    operator="lt" if long_side else "gt",
                    threshold=0.0,
                    threshold_indicator=edge,
                    threshold_period=n,
                    threshold_lookback=window,
                ),
                IndicatorCondition(
                    indicator=edge,
                    period=window,
                    operator="gt" if long_side else "lt",
                    threshold=0.0,
                    threshold_indicator=edge,
                    threshold_period=n,
                    threshold_lookback=window,
                    threshold_atr_multiple=sign * WYCKOFF_BREAK_ATR,
                ),
                IndicatorCondition(
                    indicator=poke,
                    operator="gt" if long_side else "lt",
                    threshold=0.0,
                    threshold_indicator=edge,
                    threshold_period=window,
                    threshold_lookback=1,
                ),
            )
            sos = (
                IndicatorCondition(
                    indicator=edge,
                    period=recent,
                    operator="lt" if long_side else "gt",
                    threshold=0.0,
                    threshold_indicator=edge,
                    threshold_period=n,
                    threshold_lookback=recent,
                ),
                IndicatorCondition(
                    indicator="close",
                    operator="gt" if long_side else "lt",
                    threshold=0.0,
                    threshold_indicator="open",
                    threshold_atr_multiple=-sign * 1.0,
                ),
                IndicatorCondition(
                    indicator="close",
                    operator="gt" if long_side else "lt",
                    threshold=0.0,
                    threshold_indicator="sma",
                    threshold_period=n,
                ),
            )
            families = (
                ("spring" if long_side else "upthrust", spring),
                ("spring_test" if long_side else "upthrust_test", test),
                ("spring_sos" if long_side else "upthrust_sow", sos),
            )
            for family, tail in families:
                for session_name, sessions in WYCKOFF_SESSIONS:
                    for stop, target in WYCKOFF_EXITS:
                        spec = StrategySpec(
                            schema_version=1,
                            instrument=instrument,
                            direction=direction,
                            timeframe_minutes=timeframe,  # type: ignore[arg-type]
                            entry_conditions=(*context, *tail),
                            exit_rules=(
                                StopExit(kind="stop", distance=stop, unit="atr"),
                                TargetExit(kind="target", distance=target, unit="atr"),
                                TimeStopExit(kind="time_stop", bars=_bars(36, timeframe)),
                            ),
                            tunable_parameters=trend,
                            session_filters=sessions,
                        )
                        name = (
                            f"wyckoff_{family}_{range_n5}_{session_name}_{direction}"
                            f"_{stop:g}_{target:g}"
                        )
                        result.append((SeedStrategy(name, "wyckoff", family), spec))
    return tuple(result)
