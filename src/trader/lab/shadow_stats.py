"""Diagnostic-only significance numbers; no gate reads them.

Gate 1 judges significance with a win-rate sign test against 50/50.  A strategy that
risks 1 to win 2 is profitable at a 35-40 percent win rate, which that test calls luck.
These numbers ask the question that matches such payoffs: is the average net profit
per trade above zero?  They are reported beside the gate value so the owner can see
what a payoff-aware test would say before any gate is changed.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence

from trader.lab.backtest import BacktestTrade

MIN_TRADES = 30
MIN_WEEKS = 10


def _one_sided_p(values: Sequence[float]) -> float:
    """One-sided p-value that the mean is above zero (normal approximation)."""
    count = len(values)
    if count < 2:
        return 1.0
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / (count - 1)
    if variance <= 0.0:
        return 0.0 if mean > 0 else 1.0
    statistic = mean / math.sqrt(variance / count)
    return 0.5 * math.erfc(statistic / math.sqrt(2.0))


def mean_profit_p_values(trades: Sequence[BacktestTrade]) -> dict[str, float]:
    """Per-trade and per-week p-values that average net profit is above zero.

    The weekly figure sums trades by exit week first, so trades that cluster in time
    cannot make a result look more certain than it is; it is the conservative one.
    """
    pnls = [float(trade.net_pnl) for trade in trades]
    per_trade = _one_sided_p(pnls) if len(pnls) >= MIN_TRADES else 1.0
    weeks: dict[tuple[int, int], float] = defaultdict(float)
    for trade in trades:
        iso = trade.exit_time_utc.isocalendar()
        weeks[(iso[0], iso[1])] += float(trade.net_pnl)
    weekly = _one_sided_p(list(weeks.values())) if len(weeks) >= MIN_WEEKS else 1.0
    return {
        "diag_oos_mean_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
        "diag_mean_pnl_p_trade": per_trade,
        "diag_mean_pnl_p_weekly": weekly,
    }


__all__ = ["mean_profit_p_values"]
