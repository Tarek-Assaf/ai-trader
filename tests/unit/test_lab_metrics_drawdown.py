"""Gate 1 drawdown metric is a percent of starting equity, not account currency."""

import random
from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.backtest import CostModel, run_backtest
from trader.lab.engine import EntrySignal
from trader.lab.metrics import gate1_metrics
from trader.lab.spec import IndicatorCondition, StopExit, StrategySpec, TargetExit


def test_backtest_drawdown_pct_is_percent_of_starting_equity() -> None:
    rng = random.Random(5)
    start = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    price = 1000.0
    bars: list[Candle] = []
    for index in range(2000):
        opening = price
        closing = opening + rng.gauss(0.0, 2.0)
        bars.append(
            Candle(
                ts_utc=start + timedelta(minutes=5 * index),
                open=opening,
                high=max(opening, closing) + 0.5,
                low=min(opening, closing) - 0.5,
                close=closing,
                volume=1.0,
                series_key=("NSXUSD", "histdata"),
                n_minutes=5,
            )
        )
        price = closing
    spec = StrategySpec(
        schema_version=1,
        instrument="NSXUSD",
        direction="long",
        timeframe_minutes=5,
        entry_conditions=(IndicatorCondition(indicator="close", operator="gt", threshold=0.0),),
        exit_rules=(
            StopExit(kind="stop", distance=2.0, unit="atr"),
            TargetExit(kind="target", distance=1.0, unit="atr"),
        ),
    )
    signals = tuple(
        EntrySignal("NSXUSD", 5, bar.ts_utc, bar.close) for bar in bars[30:-2] if rng.random() < 0.1
    )
    result = run_backtest(spec, bars, cost_model=CostModel(), entry_signals=signals)
    assert result.max_drawdown > 0
    metrics = gate1_metrics(result)
    expected = 100.0 * result.max_drawdown / result.starting_equity
    assert metrics["backtest_drawdown_pct"] == expected
    assert metrics["backtest_drawdown_pct"] < result.max_drawdown
