"""ATR-unit exits scale with volatility; point-unit exits do not."""

import random
from datetime import UTC, datetime, timedelta
from typing import Literal

from trader.data.resample import Candle
from trader.lab.backtest import CostModel, run_backtest
from trader.lab.engine import EntrySignal
from trader.lab.spec import IndicatorCondition, StopExit, StrategySpec, TargetExit


def _walk(scale: float, count: int = 3000, seed: int = 7) -> list[Candle]:
    rng = random.Random(seed)
    start = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    price = 1000.0 * scale
    rows: list[Candle] = []
    for index in range(count):
        opening = price
        closing = opening + rng.gauss(0.0, 1.0) * scale
        high = max(opening, closing) + abs(rng.gauss(0.0, 0.5)) * scale
        low = min(opening, closing) - abs(rng.gauss(0.0, 0.5)) * scale
        rows.append(
            Candle(
                ts_utc=start + timedelta(minutes=5 * index),
                open=opening,
                high=high,
                low=low,
                close=closing,
                volume=1.0,
                series_key=("NSXUSD", "histdata"),
                n_minutes=5,
            )
        )
        price = closing
    return rows


def _spec(unit: Literal["points", "atr"]) -> StrategySpec:
    return StrategySpec(
        schema_version=1,
        instrument="NSXUSD",
        direction="long",
        timeframe_minutes=5,
        entry_conditions=(IndicatorCondition(indicator="close", operator="gt", threshold=0.0),),
        exit_rules=(
            StopExit(kind="stop", distance=1.5, unit=unit),
            TargetExit(kind="target", distance=1.5, unit=unit),
        ),
    )


def _signals(bars: list[Candle]) -> tuple[EntrySignal, ...]:
    rng = random.Random(3)
    return tuple(
        EntrySignal("NSXUSD", 5, bar.ts_utc, bar.close)
        for bar in bars[30:-2]
        if rng.random() < 0.05
    )


def test_atr_exits_make_results_scale_with_volatility() -> None:
    small, large = _walk(1.0), _walk(10.0)
    zero = CostModel(spread_points={"NSXUSD": 0.0}, slippage_points={"NSXUSD": 0.0})
    a = run_backtest(_spec("atr"), small, cost_model=zero, entry_signals=_signals(small))
    b = run_backtest(_spec("atr"), large, cost_model=zero, entry_signals=_signals(large))
    assert len(a.trades) == len(b.trades) > 20
    assert abs(b.gross_pnl - 10.0 * a.gross_pnl) < 1e-6 * max(1.0, abs(b.gross_pnl))


def test_atr_random_entries_have_no_large_gross_edge_or_loss() -> None:
    bars = _walk(5.0, count=20000)
    zero = CostModel(spread_points={"NSXUSD": 0.0}, slippage_points={"NSXUSD": 0.0})
    result = run_backtest(_spec("atr"), bars, cost_model=zero, entry_signals=_signals(bars))
    per_trade = result.gross_pnl / len(result.trades)
    atr_scale = 5.0 * 1.5
    assert abs(per_trade) < 0.5 * atr_scale


def test_default_unit_is_points() -> None:
    assert StopExit(kind="stop", distance=1.0).unit == "points"
