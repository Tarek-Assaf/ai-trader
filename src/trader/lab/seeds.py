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
    IndicatorName,
    ParameterReference,
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


__all__ = [
    "SEED_LIBRARY",
    "SLOW_CHANNELS",
    "SeedStrategy",
    "seed_library",
    "instrument_seed_specs",
    "seed_specs",
    "level_seed_specs",
    "slow_seed_specs",
    "swing_seed_specs",
]
