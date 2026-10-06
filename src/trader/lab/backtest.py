"""Deterministic, event-driven backtests for declarative Strategy Lab specs.

The engine accepts only closed candles and a :class:`StrategySpec`.  It never
opens files, reads the sealed holdout, calls a broker, or evaluates generated
code.  Signals are generated on a completed bar and entries are filled on the
next bar's open so a signal cannot trade on its own close.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from trader.data.resample import Candle, resample_ohlc
from trader.lab.engine import evaluate_entries
from trader.lab.spec import StrategySpec


@dataclass(frozen=True, slots=True)
class CostModel:
    """Explicit price-point costs used by a backtest.

    ``session_spread_points`` may override ``spread_points`` for an instrument
    by session name.  A missing model is allowed for fixture work, but results
    are then marked provisional and never imply calibrated broker costs.
    """

    spread_points: Mapping[str, float] = field(default_factory=dict)
    slippage_points: Mapping[str, float] = field(default_factory=dict)
    session_spread_points: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    provisional: bool = True

    def __post_init__(self) -> None:
        for mapping in (self.spread_points, self.slippage_points):
            for instrument, value in mapping.items():
                if not isinstance(instrument, str) or not instrument.strip():
                    raise ValueError("cost model instruments must be non-empty")
                if not math.isfinite(float(value)) or float(value) < 0:
                    raise ValueError("cost model values must be finite and non-negative")
        for instrument, sessions in self.session_spread_points.items():
            if not isinstance(instrument, str) or not instrument.strip():
                raise ValueError("cost model instruments must be non-empty")
            for value in sessions.values():
                if not math.isfinite(float(value)) or float(value) < 0:
                    raise ValueError("session costs must be finite and non-negative")

    def for_bar(self, instrument: str, stamp: datetime) -> tuple[float, float]:
        """Return spread and slippage for the instrument/session."""
        from trader.analysis.indicators import session_tags

        spread = float(self.spread_points.get(instrument, 0.0))
        sessions = self.session_spread_points.get(instrument, {})
        for session in session_tags(stamp):
            if session in sessions:
                spread = float(sessions[session])
                break
        return spread, float(self.slippage_points.get(instrument, 0.0))


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    instrument: str
    direction: str
    entry_time_utc: datetime
    exit_time_utc: datetime
    entry_price: float
    exit_price: float
    size: float
    gross_pnl: float
    costs: float
    net_pnl: float
    exit_reason: str


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


@dataclass(frozen=True, slots=True)
class BacktestResult:
    instrument: str
    direction: str
    schema_version: int
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[tuple[datetime, float], ...]
    starting_equity: float
    ending_equity: float
    gross_pnl: float
    total_costs: float
    net_pnl: float
    max_drawdown: float
    cost_model: str
    cost_stress_pct: float
    walk_forward: tuple[WalkForwardWindow, ...]
    holdout_accessed: bool = False


@dataclass(frozen=True, slots=True)
class _OpenTrade:
    entry_index: int
    entry_time: datetime
    entry_price: float
    size: float
    stop_distance: float
    target_distance: float | None
    time_stop_bars: int | None
    trailing_distance: float | None
    trailing_activation: float | None
    spread: float
    slippage: float
    best_price: float
    trailing_stop: float | None


def walk_forward_splits(
    candles: Sequence[Candle],
    *,
    train_bars: int,
    test_bars: int,
    step_bars: int | None = None,
) -> tuple[WalkForwardWindow, ...]:
    """Return rolling train/test windows without reading beyond each test end."""
    if train_bars < 1 or test_bars < 1:
        raise ValueError("train_bars and test_bars must be positive")
    step = test_bars if step_bars is None else step_bars
    if step < 1:
        raise ValueError("step_bars must be positive")
    ordered = tuple(sorted(candles, key=lambda candle: candle.ts_utc))
    windows: list[WalkForwardWindow] = []
    start = 0
    while start + train_bars + test_bars <= len(ordered):
        train = ordered[start : start + train_bars]
        test = ordered[start + train_bars : start + train_bars + test_bars]
        windows.append(
            WalkForwardWindow(
                train_start=train[0].ts_utc,
                train_end=train[-1].ts_utc,
                test_start=test[0].ts_utc,
                test_end=test[-1].ts_utc,
            )
        )
        start += step
    return tuple(windows)


def _exit_rules(
    spec: StrategySpec,
) -> tuple[float, float | None, int | None, float | None, float | None]:
    stop: float | None = None
    target: float | None = None
    time_stop: int | None = None
    trailing_distance: float | None = None
    trailing_activation: float | None = None
    for rule in spec.exit_rules:
        if rule.kind == "stop":
            stop = float(rule.distance)
        elif rule.kind == "target":
            target = float(rule.distance)
        elif rule.kind == "time_stop":
            time_stop = rule.bars
        elif rule.kind == "trailing":
            trailing_distance = float(rule.distance)
            trailing_activation = float(rule.activation_distance)
    if stop is None:
        raise ValueError("strategy must define a stop exit")
    return stop, target, time_stop, trailing_distance, trailing_activation


def _trade_from_exit(
    spec: StrategySpec,
    position: _OpenTrade,
    bar: Candle,
    exit_price: float,
    reason: str,
    cost_multiplier: float,
) -> BacktestTrade:
    gross = (
        (exit_price - position.entry_price) * position.size
        if spec.direction == "long"
        else (position.entry_price - exit_price) * position.size
    )
    costs = (position.spread + 2 * position.slippage) * position.size * cost_multiplier
    return BacktestTrade(
        instrument=spec.instrument,
        direction=spec.direction,
        entry_time_utc=position.entry_time,
        exit_time_utc=bar.ts_utc,
        entry_price=position.entry_price,
        exit_price=exit_price,
        size=position.size,
        gross_pnl=gross,
        costs=costs,
        net_pnl=gross - costs,
        exit_reason=reason,
    )


def _exit_on_bar(
    spec: StrategySpec,
    position: _OpenTrade,
    bar: Candle,
) -> tuple[str, float] | None:
    """Return the conservative first exit, accounting for opening gaps."""
    static_stop = (
        position.entry_price - position.stop_distance
        if spec.direction == "long"
        else position.entry_price + position.stop_distance
    )
    stop_level = static_stop
    if position.trailing_stop is not None:
        stop_level = max(stop_level, position.trailing_stop) if spec.direction == "long" else min(
            stop_level, position.trailing_stop
        )
    target_level = (
        position.entry_price + position.target_distance
        if spec.direction == "long" and position.target_distance is not None
        else position.entry_price - position.target_distance
        if position.target_distance is not None
        else None
    )
    if spec.direction == "long":
        stop_reason = (
            "trailing"
            if position.trailing_stop is not None and stop_level == position.trailing_stop
            else "stop"
        )
        if bar.open <= stop_level:
            return stop_reason, bar.open
        if bar.low <= stop_level:
            return stop_reason, stop_level
        if target_level is not None:
            if bar.open >= target_level:
                return "target", bar.open
            if bar.high >= target_level:
                return "target", target_level
    else:
        stop_reason = (
            "trailing"
            if position.trailing_stop is not None and stop_level == position.trailing_stop
            else "stop"
        )
        if bar.open >= stop_level:
            return stop_reason, bar.open
        if bar.high >= stop_level:
            return stop_reason, stop_level
        if target_level is not None:
            if bar.open <= target_level:
                return "target", bar.open
            if bar.low <= target_level:
                return "target", target_level
    return None


def _ratchet_trailing(position: _OpenTrade, direction: str) -> _OpenTrade:
    if position.trailing_distance is None or position.trailing_activation is None:
        return position
    favorable = (
        position.best_price - position.entry_price
        if direction == "long"
        else position.entry_price - position.best_price
    )
    if favorable < position.trailing_activation:
        return position
    candidate = (
        position.best_price - position.trailing_distance
        if direction == "long"
        else position.best_price + position.trailing_distance
    )
    if position.trailing_stop is None:
        return replace(position, trailing_stop=candidate)
    ratchet = max(position.trailing_stop, candidate) if direction == "long" else min(
        position.trailing_stop, candidate
    )
    return replace(position, trailing_stop=ratchet)


def run_backtest(
    spec: StrategySpec,
    candles: Iterable[Candle],
    *,
    cost_model: CostModel | None = None,
    starting_equity: float = 100_000.0,
    size: float = 1.0,
    cost_stress_pct: float = 0.0,
    seed: int = 0,
    train_bars: int | None = None,
    test_bars: int | None = None,
    step_bars: int | None = None,
    holdout: bool = False,
) -> BacktestResult:
    """Run one deterministic backtest over non-holdout candles.

    ``holdout=True`` is rejected deliberately; only ``lab/validator.py`` may
    open sealed holdout data.  ``seed`` is accepted and recorded by callers so
    repeated experiments have an explicit deterministic seed even though this
    event loop itself is deterministic.
    """
    if holdout:
        raise ValueError("backtest engine cannot read the sealed holdout")
    if not math.isfinite(starting_equity) or starting_equity <= 0:
        raise ValueError("starting_equity must be positive and finite")
    if not math.isfinite(size) or size <= 0:
        raise ValueError("size must be positive and finite")
    if not math.isfinite(cost_stress_pct) or cost_stress_pct < 0:
        raise ValueError("cost_stress_pct must be finite and non-negative")
    random.Random(seed)  # Validate the seed without introducing nondeterminism.
    source = tuple(sorted(candles, key=lambda candle: candle.ts_utc))
    bars = tuple(bar for bar in resample_ohlc(source, spec.timeframe_minutes) if bar.complete)
    if not bars:
        return BacktestResult(
            spec.instrument,
            spec.direction,
            spec.schema_version,
            (),
            (),
            starting_equity,
            starting_equity,
            0.0,
            0.0,
            0.0,
            0.0,
            "provisional" if cost_model is None or cost_model.provisional else "calibrated",
            cost_stress_pct,
            (),
        )
    model = cost_model or CostModel()
    multiplier = 1.0 + cost_stress_pct / 100.0
    signals = {signal.timestamp_utc: signal for signal in evaluate_entries(spec, bars)}
    stop, target, time_stop, trailing_distance, trailing_activation = _exit_rules(spec)
    position: _OpenTrade | None = None
    trades: list[BacktestTrade] = []
    equity = starting_equity
    peak = equity
    max_drawdown = 0.0
    curve: list[tuple[datetime, float]] = []
    last_exit_index = -1
    for index, bar in enumerate(bars):
        if position is not None:
            if index > position.entry_index:
                position = _ratchet_trailing(position, spec.direction)
            exit = _exit_on_bar(spec, position, bar)
            held = index - position.entry_index
            if (
                exit is None
                and position.time_stop_bars is not None
                and held >= position.time_stop_bars
            ):
                exit = ("time_stop", bar.close)
            if exit is not None:
                reason, exit_level = exit
                trade = _trade_from_exit(spec, position, bar, exit_level, reason, multiplier)
                trades.append(trade)
                equity += trade.net_pnl
                position = None
                last_exit_index = index
            elif index >= position.entry_index:
                favorable = (
                    max(position.best_price, bar.high)
                    if spec.direction == "long"
                    else min(position.best_price, bar.low)
                )
                position = replace(position, best_price=favorable)
        if position is None and index > 0 and last_exit_index != index:
            signal = signals.get(bars[index - 1].ts_utc)
            if signal is not None and index < len(bars):
                spread, slippage = model.for_bar(spec.instrument, bar.ts_utc)
                position = _OpenTrade(
                    entry_index=index,
                    entry_time=bar.ts_utc,
                    entry_price=bar.open,
                    size=size,
                    stop_distance=stop,
                    target_distance=target,
                    time_stop_bars=time_stop,
                    trailing_distance=trailing_distance,
                    trailing_activation=trailing_activation,
                    spread=spread,
                    slippage=slippage,
                    best_price=bar.open,
                    trailing_stop=None,
                )
                entry_exit = _exit_on_bar(spec, position, bar)
                if entry_exit is not None:
                    reason, exit_level = entry_exit
                    trade = _trade_from_exit(spec, position, bar, exit_level, reason, multiplier)
                    trades.append(trade)
                    equity += trade.net_pnl
                    position = None
                    last_exit_index = index
        marked_equity = equity
        if position is not None:
            unrealized = (
                (bar.close - position.entry_price) * position.size
                if spec.direction == "long"
                else (position.entry_price - bar.close) * position.size
            )
            estimated_cost = (position.spread + 2 * position.slippage) * position.size * multiplier
            marked_equity += unrealized - estimated_cost
        peak = max(peak, marked_equity)
        max_drawdown = max(max_drawdown, peak - marked_equity)
        curve.append((bar.ts_utc, marked_equity))
    if position is not None:
        trade = _trade_from_exit(
            spec, position, bars[-1], bars[-1].close, "end_of_data", multiplier
        )
        trades.append(trade)
        equity += trade.net_pnl
        curve[-1] = (bars[-1].ts_utc, equity)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    windows = (
        walk_forward_splits(
            bars,
            train_bars=train_bars,
            test_bars=test_bars,
            step_bars=step_bars,
        )
        if train_bars is not None and test_bars is not None
        else ()
    )
    gross = math.fsum(trade.gross_pnl for trade in trades)
    costs = math.fsum(trade.costs for trade in trades)
    return BacktestResult(
        instrument=spec.instrument,
        direction=spec.direction,
        schema_version=spec.schema_version,
        trades=tuple(trades),
        equity_curve=tuple(curve),
        starting_equity=starting_equity,
        ending_equity=equity,
        gross_pnl=gross,
        total_costs=costs,
        net_pnl=gross - costs,
        max_drawdown=max_drawdown,
        cost_model="provisional" if cost_model is None or cost_model.provisional else "calibrated",
        cost_stress_pct=cost_stress_pct,
        walk_forward=windows,
    )
