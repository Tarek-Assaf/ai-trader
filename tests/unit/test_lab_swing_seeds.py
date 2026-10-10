"""Regime-filtered trend and ranging reversion seeds are valid, unique and ATR-scaled."""

import random
from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.backtest import CostModel, run_backtest
from trader.lab.seeds import swing_seed_specs


def _bars(count: int = 4000) -> list[Candle]:
    rng = random.Random(5)
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    price = 1000.0
    rows: list[Candle] = []
    for index in range(count):
        opening = price
        closing = opening + rng.gauss(0.0, 1.0)
        rows.append(
            Candle(
                ts_utc=start + timedelta(minutes=5 * index),
                open=opening,
                high=max(opening, closing) + 0.4,
                low=min(opening, closing) - 0.4,
                close=closing,
                volume=1.0,
                series_key=("NSXUSD", "histdata"),
                n_minutes=5,
            )
        )
        price = closing
    return rows


def test_swing_seeds_are_unique_valid_atr_scaled_and_regime_filtered() -> None:
    seeds = swing_seed_specs("NSXUSD", 5)
    names = [seed.name for seed, _ in seeds]
    assert len(names) == len(set(names)) == 24
    for _, spec in seeds:
        assert spec.regime_filters
        exits = [rule for rule in spec.exit_rules if rule.kind in {"stop", "target"}]
        assert exits and all(rule.unit == "atr" for rule in exits)
        assert any(rule.kind == "time_stop" for rule in spec.exit_rules)


def test_swing_seeds_run_and_trade_rarely() -> None:
    bars = _bars()
    zero = CostModel(spread_points={"NSXUSD": 0.0}, slippage_points={"NSXUSD": 0.0})
    days = len(bars) / 288
    for _, spec in swing_seed_specs("NSXUSD", 5):
        result = run_backtest(spec, bars, cost_model=zero)
        assert len(result.trades) / days < 8.0
