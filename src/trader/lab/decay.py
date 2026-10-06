"""Deterministic rolling decay and demotion rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
        window_trades: int,
        max_drawdown_ratio: float,
        size_reduction: float,
        retire_days: int,
    ) -> None:
        if window_trades < 1 or not 0 < size_reduction < 1:
            raise ValueError("decay settings are invalid")
        self.window_trades = window_trades
        self.max_drawdown_ratio = max_drawdown_ratio
        self.size_reduction = size_reduction
        self.retire_days = retire_days

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> DecayMonitor:
        values = config.get("demotion")
        if not isinstance(values, Mapping):
            raise ValueError("demotion config is missing")
        required = {
            "rolling_expectancy_window_trades",
            "live_drawdown_vs_backtest_max_ratio",
            "size_reduction_on_demotion",
            "retire_on_second_demotion_within_days",
        }
        if set(values) != required:
            raise ValueError("demotion config has missing or unknown keys")
        return cls(
            window_trades=int(values["rolling_expectancy_window_trades"]),
            max_drawdown_ratio=float(values["live_drawdown_vs_backtest_max_ratio"]),
            size_reduction=float(values["size_reduction_on_demotion"]),
            retire_days=int(values["retire_on_second_demotion_within_days"]),
        )

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
        equity = peak = 0.0
        drawdown = 0.0
        for value in sample:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
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
