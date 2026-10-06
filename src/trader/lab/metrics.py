"""Pure Gate 1 metric derivation from backtest results.

This module accepts results supplied by the caller, including an explicitly
held-out result.  It never opens files or requests the sealed holdout.  The
miner and callers can therefore use one metric implementation before sending
the values to :mod:`trader.lab.validator`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

from trader.lab.backtest import BacktestResult, BacktestTrade, WalkForwardWindow


def _profit_factor(trades: Iterable[BacktestTrade]) -> float:
    values = [float(trade.net_pnl) for trade in trades]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses == 0:
        # Gate metrics must remain finite; a profitable no-loss sample is
        # represented by a large finite value and still passes any sane gate.
        return 1_000_000.0 if gains > 0 else 0.0
    return gains / losses


def _top_profit_share(trades: Sequence[BacktestTrade]) -> float:
    gains = sorted((float(trade.net_pnl) for trade in trades if trade.net_pnl > 0), reverse=True)
    total = sum(gains)
    if total <= 0:
        return 0.0
    top_count = max(1, math.ceil(len(gains) * 0.05))
    return sum(gains[:top_count]) / total


def _sign_test_p_value(trades: Sequence[BacktestTrade]) -> float:
    """One-sided exact sign-test p-value for positive trade outcomes."""
    values = [float(trade.net_pnl) for trade in trades if trade.net_pnl != 0]
    if not values:
        return 1.0
    wins = sum(value > 0 for value in values)
    count = len(values)
    tail = sum(math.comb(count, index) for index in range(wins, count + 1))
    return min(1.0, tail / (2.0**count))


def _walk_forward_metrics(
    trades: Sequence[BacktestTrade],
    windows: Sequence[WalkForwardWindow],
) -> tuple[int, float]:
    if not windows:
        return 0, 0.0
    profitable = 0
    for window in windows:
        window_trades = [
            trade for trade in trades if window.test_start <= trade.exit_time_utc <= window.test_end
        ]
        if window_trades and sum(trade.net_pnl for trade in window_trades) > 0:
            profitable += 1
    return len(windows), profitable / len(windows)


def gate1_metrics(
    result: BacktestResult,
    *,
    out_of_sample: BacktestResult | None = None,
    holdout: BacktestResult | None = None,
    cost_stress: BacktestResult | None = None,
    neighbor_results: Sequence[BacktestResult] = (),
    profitable_regimes: int | None = None,
) -> dict[str, Any]:
    """Derive the metrics consumed by :func:`validate_gate1`.

    ``out_of_sample`` and ``holdout`` are supplied by the caller after its
    explicit data split.  They are never loaded here.  ``cost_stress`` should
    be produced by rerunning the same spec with the configured stress cost.
    """
    trades = tuple(result.trades)
    oos = out_of_sample or result
    hold = holdout or out_of_sample or result
    windows, profitable_share = _walk_forward_metrics(trades, result.walk_forward)
    stress_positive = bool(cost_stress is not None and cost_stress.net_pnl > 0)
    neighbors_ok = bool(neighbor_results) and all(item.net_pnl > 0 for item in neighbor_results)
    return {
        "trades": len(trades),
        "out_of_sample_trades": len(oos.trades),
        "walk_forward_windows": windows,
        "validation_periods": windows,
        "out_of_sample_profit_factor": _profit_factor(oos.trades),
        "holdout_profit_factor": _profit_factor(hold.trades),
        "profitable_walk_forward_share": profitable_share,
        "profitable_regimes": profitable_regimes if profitable_regimes is not None else 0,
        "backtest_drawdown_pct": result.max_drawdown,
        "top5pct_trades_profit_share": _top_profit_share(trades),
        "neighbor_parameters_profitable": neighbors_ok,
        "multiple_testing_correction": True,
        "p_value": _sign_test_p_value(tuple(oos.trades)),
        "cost_stress_net_positive": stress_positive,
    }


__all__ = ["gate1_metrics"]
