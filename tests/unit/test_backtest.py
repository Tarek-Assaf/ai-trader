"""Deterministic Strategy Lab backtest fixtures."""

from datetime import UTC, datetime, timedelta

import pytest

from trader.data.resample import Candle
from trader.lab.backtest import CostModel, run_backtest, walk_forward_splits
from trader.lab.spec import ComplexityLimits, parse_strategy_spec


LIMITS = ComplexityLimits(max_conditions_per_strategy=5, max_tunable_parameters=4)


def _spec(**overrides: object):
    value: dict[str, object] = {
        "schema_version": 1,
        "instrument": "NAS100",
        "direction": "long",
        "timeframe_minutes": 1,
        "entry_conditions": [{"indicator": "close", "operator": "gt", "threshold": 99.0}],
        "exit_rules": [
            {"kind": "stop", "distance": 2.0},
            {"kind": "target", "distance": 2.0},
        ],
        "tunable_parameters": [],
        "regime_filters": [],
        "session_filters": [],
    }
    value.update(overrides)
    return parse_strategy_spec(value, complexity_limits=LIMITS)


def _bars(count: int = 5) -> list[Candle]:
    start = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    values = [100.0, 100.0, 102.5, 102.5, 102.5][:count]
    return [
        Candle(
            ts_utc=start + timedelta(minutes=index),
            open=value,
            high=value + (1.5 if index == 2 else 0.2),
            low=value - 0.2,
            close=value,
            volume=1.0,
            series_key=("NAS100", "bid"),
        )
        for index, value in enumerate(values)
    ]


def test_entry_fills_next_open_and_cost_stress_reduces_net():
    spec = _spec()
    bars = _bars()
    plain = run_backtest(spec, bars, cost_model=CostModel(spread_points={"NAS100": 0.5}))
    stressed = run_backtest(
        spec,
        bars,
        cost_model=CostModel(spread_points={"NAS100": 0.5}),
        cost_stress_pct=100,
    )
    assert plain.trades
    assert plain.trades[0].entry_time_utc == bars[1].ts_utc
    assert plain.trades[0].entry_price == bars[1].open
    assert stressed.net_pnl < plain.net_pnl
    assert plain.cost_model == "provisional"


def test_holdout_is_rejected_and_walk_forward_windows_are_rolling():
    spec = _spec()
    bars = _bars(5)
    with pytest.raises(ValueError, match="sealed holdout"):
        run_backtest(spec, bars, holdout=True)
    windows = walk_forward_splits(bars, train_bars=2, test_bars=2)
    assert len(windows) == 1
    assert windows[0].train_end < windows[0].test_start


def test_truncating_future_bars_does_not_change_prior_trade():
    spec = _spec()
    full = run_backtest(spec, _bars(), cost_model=CostModel())
    truncated = run_backtest(spec, _bars(3), cost_model=CostModel())
    assert full.trades[0] == truncated.trades[0]
