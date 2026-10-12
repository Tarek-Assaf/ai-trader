"""Random-entry baseline: drift alone must not look like skill."""

import random
from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.backtest import CostModel
from trader.lab.random_baseline import random_entry_baseline
from trader.lab.spec import IndicatorCondition, StopExit, StrategySpec, TargetExit, TimeStopExit


def _drifting_market(minutes: int, drift: float, seed: int = 1) -> list[Candle]:
    rng = random.Random(seed)
    price = 100.0
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    bars: list[Candle] = []
    for index in range(minutes):
        move = drift + rng.gauss(0.0, 0.15)
        open_ = price
        price = max(1.0, price + move)
        high = max(open_, price) + 0.05
        low = min(open_, price) - 0.05
        bars.append(
            Candle(
                ts_utc=start + timedelta(minutes=index),
                open=open_,
                high=high,
                low=low,
                close=price,
                volume=1.0,
                series_key=("NSXUSD", "histdata"),
            )
        )
    return bars


def _always_long() -> StrategySpec:
    return StrategySpec(
        schema_version=1,
        instrument="NSXUSD",
        direction="long",
        timeframe_minutes=5,
        entry_conditions=(IndicatorCondition(indicator="close", operator="gt", threshold=0.0),),
        exit_rules=(
            StopExit(kind="stop", distance=1.5, unit="atr"),
            TargetExit(kind="target", distance=3.0, unit="atr"),
            TimeStopExit(kind="time_stop", bars=24),
        ),
    )


def test_drift_driven_long_idea_sits_mid_distribution() -> None:
    candles = _drifting_market(60 * 24 * 12, drift=0.004)
    result = random_entry_baseline(
        _always_long(), candles, cost_model=CostModel(), trials=20, seed=3
    )
    assert result["trials"] == 20
    assert result["trades"] > 20
    # Entering on every bar is just a random long entry: it must not stand out.
    assert 0.0 <= float(result["strategy_percentile"]) <= 1.0  # type: ignore[arg-type]
    assert abs(float(result["z_vs_random"])) < 3.0  # type: ignore[arg-type]


def test_baseline_is_deterministic_and_flags_no_trade_strategies() -> None:
    candles = _drifting_market(60 * 24 * 4, drift=0.0)
    first = random_entry_baseline(_always_long(), candles, trials=10, seed=5)
    second = random_entry_baseline(_always_long(), candles, trials=10, seed=5)
    assert first == second
    never = _always_long().model_copy(
        update={
            "entry_conditions": (
                IndicatorCondition(indicator="close", operator="lt", threshold=0.0),
            )
        }
    )
    assert random_entry_baseline(never, candles, trials=10)["note"] == "strategy produced no trades"
