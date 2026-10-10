#!/usr/bin/env python3
"""Run offline Strategy Lab control experiments.

The controls deliberately use only the existing HistData loader, declarative
``StrategySpec`` values and the deterministic backtest engine.  They never
open the sealed holdout, call a broker, place orders or evaluate Gate 1.
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trader.data.resample import Candle, resample_ohlc
from trader.lab.backtest import BacktestResult, CostModel, run_backtest
from trader.lab.engine import EntrySignal
from trader.lab.runner import (
    INSTRUMENTS,
    holdout_months,
    load_histdata_candles,
    pessimistic_cost_model,
    strategy_lab_max_training_years,
)
from trader.lab.seeds import seed_specs
from trader.lab.spec import IndicatorCondition, StopExit, StrategySpec, TargetExit

TIMEFRAME_MINUTES = 5
RANDOM_TRADES_PER_DAY = 15.0
RANDOM_SEEDS = (0, 1, 2, 3, 4)
RANDOM_STOP = 1.5
RANDOM_TARGET = 1.0
PERFECT_STOP = 1_000_000.0
PERFECT_TARGET = 0.25
CONTROL_FAMILIES = (
    "bollinger_reversion",
    "donchian_breakout",
    "ema_crossover_atr_stop",
    "ema_pullback",
    "rsi_mean_reversion",
)


@dataclass(frozen=True, slots=True)
class ControlMetrics:
    """Small JSON-safe summary shared by the CLI and unit tests."""

    trades: int
    gross_pnl: float
    gross_per_trade: float
    costs: float
    net_pnl: float
    win_rate: float

    @classmethod
    def from_result(cls, result: BacktestResult) -> ControlMetrics:
        count = len(result.trades)
        wins = sum(1 for trade in result.trades if trade.gross_pnl > 0)
        return cls(
            trades=count,
            gross_pnl=float(result.gross_pnl),
            gross_per_trade=float(result.gross_pnl) / count if count else 0.0,
            costs=float(result.total_costs),
            net_pnl=float(result.net_pnl),
            win_rate=wins / count if count else 0.0,
        )

    def as_dict(self) -> dict[str, float | int]:
        return {
            "trades": self.trades,
            "gross_pnl": self.gross_pnl,
            "gross_per_trade": self.gross_per_trade,
            "costs": self.costs,
            "net_pnl": self.net_pnl,
            "win_rate": self.win_rate,
        }


def _bars(candles: Iterable[Candle], timeframe: int) -> tuple[Candle, ...]:
    return tuple(item for item in resample_ohlc(tuple(candles), timeframe) if item.complete)


def _span_days(candles: Iterable[Candle]) -> float:
    ordered = tuple(candles)
    if len(ordered) < 2:
        return 1.0
    start = min(candle.ts_utc for candle in ordered)
    end = max(candle.ts_utc for candle in ordered)
    return max(1.0, (end - start).total_seconds() / 86_400.0)


def _signals(
    candles: Iterable[Candle], *, instrument: str, timeframe: int, seed: int
) -> tuple[tuple[EntrySignal, ...], tuple[EntrySignal, ...]]:
    """Return deterministic random long and short entries at about 15/day."""
    bars = _bars(candles, timeframe)
    if not bars:
        return (), ()
    probability = RANDOM_TRADES_PER_DAY / (24.0 * 60.0 / timeframe)
    generator = random.Random(seed)
    long_entries: list[EntrySignal] = []
    short_entries: list[EntrySignal] = []
    for current in bars[:-1]:
        if generator.random() >= probability:
            continue
        signal = EntrySignal(instrument, timeframe, current.ts_utc, current.close)
        if generator.random() < 0.5:
            long_entries.append(signal)
        else:
            short_entries.append(signal)
    return tuple(long_entries), tuple(short_entries)


def _with_exit_rules(spec: StrategySpec, *, stop: float, target: float) -> StrategySpec:
    updated: StrategySpec = spec.model_copy(
        update={
            "exit_rules": (
                StopExit(kind="stop", distance=stop),
                TargetExit(kind="target", distance=target),
            )
        }
    )
    return updated


def _ema_pullback_specs(instrument: str, timeframe: int) -> tuple[StrategySpec, StrategySpec]:
    base = next(
        spec for seed, spec in seed_specs(instrument, timeframe) if seed.name == "ema_pullback"
    )
    long_spec = _with_exit_rules(base, stop=RANDOM_STOP, target=RANDOM_TARGET)
    return long_spec, long_spec.model_copy(update={"direction": "short"})


def random_entry_controls(
    candles: Iterable[Candle],
    *,
    instrument: str,
    timeframe: int = TIMEFRAME_MINUTES,
    cost_model: CostModel | None = None,
    seeds: tuple[int, ...] = RANDOM_SEEDS,
) -> dict[str, Any]:
    """Run five random-direction controls using ema_pullback exits."""
    candles = tuple(candles)
    days = _span_days(candles)
    long_spec, short_spec = _ema_pullback_specs(instrument, timeframe)
    results: list[dict[str, Any]] = []
    for seed in seeds:
        long_signals, short_signals = _signals(
            candles, instrument=instrument, timeframe=timeframe, seed=seed
        )
        long_result = run_backtest(
            long_spec,
            candles,
            cost_model=cost_model,
            entry_signals=long_signals,
        )
        short_result = run_backtest(
            short_spec,
            candles,
            cost_model=cost_model,
            entry_signals=short_signals,
        )
        combined = ControlMetrics(
            trades=len(long_result.trades) + len(short_result.trades),
            gross_pnl=long_result.gross_pnl + short_result.gross_pnl,
            gross_per_trade=(
                (long_result.gross_pnl + short_result.gross_pnl)
                / (len(long_result.trades) + len(short_result.trades))
                if long_result.trades or short_result.trades
                else 0.0
            ),
            costs=long_result.total_costs + short_result.total_costs,
            net_pnl=long_result.net_pnl + short_result.net_pnl,
            win_rate=(
                sum(trade.gross_pnl > 0 for trade in long_result.trades + short_result.trades)
                / (len(long_result.trades) + len(short_result.trades))
                if long_result.trades or short_result.trades
                else 0.0
            ),
        )
        results.append(
            {
                "seed": seed,
                "trades_per_day": combined.trades / days,
                **combined.as_dict(),
            }
        )
    return {
        "trades_per_day_target": RANDOM_TRADES_PER_DAY,
        "stop_points": RANDOM_STOP,
        "target_points": RANDOM_TARGET,
        "seeds": results,
    }


def zero_cost_controls(
    candles: Iterable[Candle],
    *,
    instrument: str,
    timeframe: int = TIMEFRAME_MINUTES,
) -> dict[str, dict[str, float | int]]:
    """Report pure gross PnL for one base spec from each seed family."""
    candles = tuple(candles)
    zero = CostModel(spread_points={instrument: 0.0}, slippage_points={instrument: 0.0})
    output: dict[str, dict[str, float | int]] = {}
    for seed, spec in seed_specs(instrument, timeframe):
        if seed.name not in CONTROL_FAMILIES:
            continue
        output[seed.name] = ControlMetrics.from_result(
            run_backtest(spec, candles, cost_model=zero)
        ).as_dict()
    return output


def _perfect_spec(instrument: str, timeframe: int, direction: str) -> StrategySpec:
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction=direction,
        timeframe_minutes=timeframe,
        entry_conditions=(
            # Explicit entry_signals below replace this harmless declarative condition.
            IndicatorCondition(indicator="close", operator="gt", threshold=0.0),
        ),
        exit_rules=(
            StopExit(kind="stop", distance=PERFECT_STOP),
            TargetExit(kind="target", distance=PERFECT_TARGET),
        ),
    )


def perfect_foresight_control(
    candles: Iterable[Candle],
    *,
    instrument: str,
    timeframe: int = TIMEFRAME_MINUTES,
    cost_model: CostModel | None = None,
) -> ControlMetrics:
    """Enter in the direction of the next bar while retaining engine fills."""
    candles = tuple(candles)
    bars = _bars(candles, timeframe)
    long_signals: list[EntrySignal] = []
    short_signals: list[EntrySignal] = []
    for current, following in zip(bars, bars[1:], strict=False):
        signal = EntrySignal(instrument, timeframe, current.ts_utc, current.close)
        if following.close > following.open:
            long_signals.append(signal)
        elif following.close < following.open:
            short_signals.append(signal)
    long = run_backtest(
        _perfect_spec(instrument, timeframe, "long"),
        candles,
        cost_model=cost_model,
        entry_signals=tuple(long_signals),
    )
    short = run_backtest(
        _perfect_spec(instrument, timeframe, "short"),
        candles,
        cost_model=cost_model,
        entry_signals=tuple(short_signals),
    )
    trades = long.trades + short.trades
    return ControlMetrics(
        trades=len(trades),
        gross_pnl=long.gross_pnl + short.gross_pnl,
        gross_per_trade=(long.gross_pnl + short.gross_pnl) / len(trades) if trades else 0.0,
        costs=long.total_costs + short.total_costs,
        net_pnl=long.net_pnl + short.net_pnl,
        win_rate=sum(trade.gross_pnl > 0 for trade in trades) / len(trades) if trades else 0.0,
    )


def cost_audit(
    candles: Iterable[Candle], *, instrument: str, cost_model: CostModel
) -> dict[str, float | int | str]:
    """Explain configured spread/slippage and their share of the bar range."""
    candles = tuple(candles)
    bars = _bars(candles, TIMEFRAME_MINUTES)
    if not bars:
        return {
            "instrument": instrument,
            "spread_points": 0.0,
            "slippage_points": 0.0,
            "commission_points": 0.0,
            "cost_per_trade_points": 0.0,
            "average_bar_range_points": 0.0,
            "cost_share_of_average_bar_range": 0.0,
            "commission_note": "commission is not modeled by Strategy Lab CostModel",
        }
    spread_values: list[float] = []
    slippage_values: list[float] = []
    for bar in bars:
        spread, slippage = cost_model.for_bar(instrument, bar.ts_utc)
        spread_values.append(spread)
        slippage_values.append(slippage)
    spread = sum(spread_values) / len(spread_values)
    slippage = sum(slippage_values) / len(slippage_values)
    cost = spread + 2.0 * slippage
    average_range = sum(max(0.0, bar.high - bar.low) for bar in bars) / len(bars)
    return {
        "instrument": instrument,
        "spread_points": spread,
        "slippage_points": slippage,
        "commission_points": 0.0,
        "cost_per_trade_points": cost,
        "average_bar_range_points": average_range,
        "cost_share_of_average_bar_range": cost / average_range if average_range else 0.0,
        "commission_note": "commission is not modeled by Strategy Lab CostModel",
    }


def _training_candles(
    lake: Path,
    holdout: Path,
    instrument: str,
    *,
    years: int | None,
    timeframe: int,
) -> tuple[Candle, ...]:
    excluded = holdout_months(holdout, (instrument,))
    if not excluded:
        raise RuntimeError(
            "sealed holdout months are required to bound controls to the training window"
        )
    candles = load_histdata_candles(lake, instrument, timeframe, exclude_months=excluded)
    if years is not None and excluded:
        first_holdout = min(excluded)
        cutoff = datetime(first_holdout[0] - years, first_holdout[1], 1, tzinfo=UTC)
        candles = tuple(item for item in candles if item.ts_utc >= cutoff)
    return candles


def run_controls(
    *,
    lake: Path,
    holdout: Path,
    config: Path,
    instruments: tuple[str, ...] = INSTRUMENTS,
    timeframe: int = TIMEFRAME_MINUTES,
    years: int | None = 6,
) -> dict[str, Any]:
    cost_model = pessimistic_cost_model(config)
    output: dict[str, Any] = {
        "timeframe_minutes": timeframe,
        "training_years": years,
        "instruments": {},
        "holdout_accessed": False,
        "gates_evaluated": False,
    }
    for instrument in instruments:
        candles = _training_candles(lake, holdout, instrument, years=years, timeframe=timeframe)
        output["instruments"][instrument] = {
            "bars": len(candles),
            "random_entry": random_entry_controls(
                candles, instrument=instrument, timeframe=timeframe, cost_model=cost_model
            ),
            "zero_cost": zero_cost_controls(candles, instrument=instrument, timeframe=timeframe),
            "perfect_foresight": perfect_foresight_control(
                candles, instrument=instrument, timeframe=timeframe, cost_model=cost_model
            ).as_dict(),
            "cost_audit": cost_audit(candles, instrument=instrument, cost_model=cost_model),
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline Strategy Lab controls")
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--holdout", type=Path, default=Path("/workspace/data/holdout"))
    parser.add_argument("--config", type=Path, default=Path("config/strategy_lab.yaml"))
    parser.add_argument("--instruments", nargs="+", default=list(INSTRUMENTS))
    parser.add_argument("--timeframe", type=int, default=TIMEFRAME_MINUTES)
    parser.add_argument("--years", help="training history in years, or 'all'", default=None)
    args = parser.parse_args()
    if args.years is None:
        years = strategy_lab_max_training_years(args.config)
    elif args.years.lower() == "all":
        years = None
    else:
        years = int(args.years)
        if years < 1:
            raise SystemExit("--years must be a positive integer or all")
    payload = run_controls(
        lake=args.lake,
        holdout=args.holdout,
        config=args.config,
        instruments=tuple(args.instruments),
        timeframe=args.timeframe,
        years=years,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
