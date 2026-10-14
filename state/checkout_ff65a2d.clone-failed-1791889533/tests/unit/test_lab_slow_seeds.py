"""Slow trend seeds are valid, unique, slow and ATR-scaled."""

import random
from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.backtest import CostModel, run_backtest
from trader.lab.seeds import slow_seed_specs


def _trend(count: int = 4000) -> list[Candle]:
    rng = random.Random(11)
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    price = 1000.0
    rows: list[Candle] = []
    for index in range(count):
        opening = price
        closing = opening + 0.05 + rng.gauss(0.0, 1.0)
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


def test_slow_seeds_are_unique_valid_atr_scaled() -> None:
    seeds = slow_seed_specs("NSXUSD", 5)
    names = [seed.name for seed, _ in seeds]
    assert len(names) == len(set(names)) == 24
    for _, spec in seeds:
        stops = [rule for rule in spec.exit_rules if rule.kind in {"stop", "target"}]
        assert stops and all(rule.unit == "atr" for rule in stops)
        assert any(rule.kind == "time_stop" for rule in spec.exit_rules)


def test_slow_seeds_trade_rarely() -> None:
    bars = _trend()
    zero = CostModel(spread_points={"NSXUSD": 0.0}, slippage_points={"NSXUSD": 0.0})
    days = len(bars) / 288
    for _, spec in slow_seed_specs("NSXUSD", 5):
        if "new_york" in spec.session_filters:
            continue
        result = run_backtest(spec, bars, cost_model=zero)
        assert len(result.trades) / days < 8.0
