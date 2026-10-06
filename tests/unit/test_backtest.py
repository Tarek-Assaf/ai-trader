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


def _ohlc(rows: list[tuple[float, float, float, float]]) -> list[Candle]:
    start = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    return [
        Candle(
            ts_utc=start + timedelta(minutes=index),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            series_key=("NAS100", "bid"),
        )
        for index, (open_price, high, low, close) in enumerate(rows)
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
    assert plain.trades[0].exit_price == 102.5
    assert plain.trades[0].gross_pnl == 2.5
    assert plain.trades[0].costs == 0.5
    assert plain.trades[0].net_pnl == 2.0
    assert plain.trades[0].exit_reason == "target"
    assert stressed.net_pnl < plain.net_pnl
    assert plain.cost_model == "provisional"


def test_short_trade_has_exact_directional_pnl():
    spec = _spec(
        direction="short",
        entry_conditions=[{"indicator": "close", "operator": "lt", "threshold": 101.0}],
    )
    result = run_backtest(
        spec,
        _ohlc([(100, 101, 99, 100), (100, 101, 99, 100), (98, 99, 97, 98)]),
        cost_model=CostModel(spread_points={"NAS100": 0.25}),
    )
    trade = result.trades[0]
    assert trade.entry_price == 100.0
    assert trade.exit_price == 98.0
    assert trade.gross_pnl == 2.0
    assert trade.costs == 0.25
    assert trade.net_pnl == 1.75


def test_gap_stop_fills_at_worse_open_and_blocks_same_bar_reentry():
    spec = _spec()
    result = run_backtest(
        spec,
        _ohlc([(100, 101, 99, 100), (100, 101, 99, 100), (95, 96, 94, 95)]),
        cost_model=CostModel(),
    )
    assert result.trades[0].exit_reason == "stop"
    assert result.trades[0].exit_price == 95.0
    assert all(
        trade.entry_time_utc != result.trades[0].exit_time_utc for trade in result.trades[1:]
    )


def test_trailing_stop_activates_on_closed_bar_and_ratchets():
    spec = _spec(
        exit_rules=[
            {"kind": "stop", "distance": 2.0},
            {"kind": "trailing", "distance": 1.0, "activation_distance": 1.0},
        ]
    )
    result = run_backtest(
        spec,
        _ohlc(
            [
                (100, 100.5, 99.5, 100),
                (100, 102.0, 99.5, 101),
                (102, 103.0, 101.5, 102),
                (102, 102.5, 101.0, 101.5),
            ]
        ),
        cost_model=CostModel(),
    )
    assert result.trades[0].exit_reason == "trailing"
    assert result.trades[0].exit_price == 102.0


def test_holdout_is_rejected_and_walk_forward_windows_are_rolling():
    spec = _spec()
    bars = _bars(5)
    with pytest.raises(ValueError, match="sealed holdout"):
        run_backtest(spec, bars, holdout=True)
    windows = walk_forward_splits(bars, train_bars=2, test_bars=2)
    assert len(windows) == 1
    assert windows[0].train_end < windows[0].test_start

    two_windows = walk_forward_splits(
        _ohlc([(100, 101, 99, 100)] * 6), train_bars=2, test_bars=2, step_bars=2
    )
    assert len(two_windows) == 2


def test_truncating_future_bars_does_not_change_prior_trade():
    spec = _spec()
    full = run_backtest(spec, _bars(), cost_model=CostModel())
    truncated = run_backtest(spec, _bars(3), cost_model=CostModel())
    assert full.trades[0] == truncated.trades[0]
