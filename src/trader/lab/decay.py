"""Deterministic rolling decay and demotion rules."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, slots=True)
class DecayDecision:
    strategy_id: str
    demote: bool
    retire: bool
    reason: str
    size_multiplier: float


class DecayMonitor:
    def __init__(
        self,
        *,
        window_trades: int = 30,
        max_drawdown_ratio: float = 1.5,
        size_reduction: float = 0.5,
        retire_days: int = 90,
    ) -> None:
        if window_trades < 1 or not 0 < size_reduction < 1:
            raise ValueError("decay settings are invalid")
        self.window_trades = window_trades
        self.max_drawdown_ratio = max_drawdown_ratio
        self.size_reduction = size_reduction
        self.retire_days = retire_days

    def evaluate(
        self,
        strategy_id: str,
        returns: Sequence[float],
        *,
        backtest_drawdown: float,
        demotions: Sequence[datetime] = (),
        now: datetime | None = None,
    ) -> DecayDecision:
        if backtest_drawdown <= 0:
            raise ValueError("backtest_drawdown must be positive")
        sample = tuple(returns[-self.window_trades :])
        drawdown = abs(min(0.0, min(sample, default=0.0)))
        expectancy = sum(sample) / len(sample) if sample else 0.0
        demote = drawdown / backtest_drawdown > self.max_drawdown_ratio or expectancy < 0
        clock = now or datetime.now(UTC)
        recent = [item for item in demotions if clock - item <= timedelta(days=self.retire_days)]
        retire = demote and bool(recent)
        return DecayDecision(
            strategy_id,
            demote,
            retire,
            "rolling_drawdown_or_negative_expectancy" if demote else "within_decay_limits",
            self.size_reduction if demote else 1.0,
        )


__all__ = ["DecayDecision", "DecayMonitor"]
