"""Deterministic Strategy Lab gates and sealed-holdout access.

Only this module exposes :class:`HoldoutReader`; the backtest and search
modules accept ordinary candle data and cannot request the sealed holdout.
Gate 1 uses a Bonferroni correction over the immutable experiment count so a
large random search cannot turn a lucky result into a promotion.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trader.lab.ledger import ExperimentLedger


class HoldoutAccessError(PermissionError):
    """Raised when a holdout request is malformed or outside the reader."""


class HoldoutReader:
    """The sole holdout reader; callers receive bytes and no mutable path."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def read(self, path: str | Path) -> bytes:
        candidate = Path(path).resolve()
        if self._root not in candidate.parents and candidate != self._root:
            raise HoldoutAccessError("holdout path is outside the sealed root")
        if not candidate.is_file():
            raise HoldoutAccessError("sealed holdout file does not exist")
        return candidate.read_bytes()


def strategy_hash(spec: Mapping[str, Any]) -> str:
    """Return the stable hash used to join experiments and holdout reads."""
    payload = json.dumps(dict(spec), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bonferroni_alpha(alpha: float, trials: int) -> float:
    """Split a family-wise error budget across all attempted variants."""
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between zero and one")
    if trials < 1:
        raise ValueError("trials must be positive")
    return alpha / trials


@dataclass(frozen=True, slots=True)
class Gate1Config:
    min_trades: int = 300
    min_out_of_sample_trades: int = 100
    min_walk_forward_windows: int = 5
    min_validation_periods: int = 3
    min_out_of_sample_profit_factor: float = 1.3
    min_holdout_profit_factor: float = 1.2
    min_profitable_walk_forward_share: float = 0.70
    cost_stress_increase_pct: float = 50.0
    require_neighbor_parameters_profitable: bool = True
    require_multiple_testing_correction: bool = True
    min_profitable_regimes: int = 2
    max_top5pct_trades_profit_share: float = 0.40
    max_backtest_drawdown_pct: float = 20.0
    alpha: float = 0.05


@dataclass(frozen=True, slots=True)
class ValidationResult:
    passed: bool
    reasons: tuple[str, ...]
    adjusted_alpha: float
    trials: int
    holdout_read: bool = False
    metrics: Mapping[str, Any] = field(default_factory=dict)


def validate_gate1(
    metrics: Mapping[str, Any],
    *,
    ledger: ExperimentLedger | None = None,
    trials: int | None = None,
    config: Gate1Config = Gate1Config(),
    holdout_read: bool = False,
) -> ValidationResult:
    """Apply Gate 1 using named metrics and fail closed when one is absent."""
    count = trials if trials is not None else (ledger.count() if ledger is not None else 1)
    adjusted = bonferroni_alpha(config.alpha, max(1, count))
    required: dict[str, tuple[str, float, str]] = {
        "trades": ("min_trades", config.min_trades, "too_few_trades"),
        "out_of_sample_trades": (
            "min_out_of_sample_trades",
            config.min_out_of_sample_trades,
            "too_few_out_of_sample_trades",
        ),
        "walk_forward_windows": (
            "min_walk_forward_windows",
            config.min_walk_forward_windows,
            "too_few_walk_forward_windows",
        ),
        "validation_periods": (
            "min_validation_periods",
            config.min_validation_periods,
            "too_few_validation_periods",
        ),
        "out_of_sample_profit_factor": (
            "min_out_of_sample_profit_factor",
            config.min_out_of_sample_profit_factor,
            "out_of_sample_profit_factor_below_gate",
        ),
        "holdout_profit_factor": (
            "min_holdout_profit_factor",
            config.min_holdout_profit_factor,
            "holdout_profit_factor_below_gate",
        ),
        "profitable_walk_forward_share": (
            "min_profitable_walk_forward_share",
            config.min_profitable_walk_forward_share,
            "walk_forward_share_below_gate",
        ),
        "profitable_regimes": (
            "min_profitable_regimes",
            config.min_profitable_regimes,
            "too_few_profitable_regimes",
        ),
    }
    reasons: list[str] = []
    for name, (_, minimum, reason) in required.items():
        value = metrics.get(name)
        if value is None or float(value) < minimum:
            reasons.append(reason)
    drawdown = metrics.get("backtest_drawdown_pct")
    if drawdown is None or float(drawdown) > config.max_backtest_drawdown_pct:
        reasons.append("backtest_drawdown_above_gate")
    top_share = metrics.get("top5pct_trades_profit_share")
    if top_share is None or float(top_share) > config.max_top5pct_trades_profit_share:
        reasons.append("profit_concentration_above_gate")
    if config.require_neighbor_parameters_profitable and not metrics.get(
        "neighbor_parameters_profitable", False
    ):
        reasons.append("neighbor_parameters_not_profitable")
    if config.require_multiple_testing_correction and not metrics.get(
        "multiple_testing_corrected", False
    ):
        reasons.append("multiple_testing_correction_missing")
    if metrics.get("p_value") is not None and float(metrics["p_value"]) > adjusted:
        reasons.append("adjusted_significance_failed")
    if holdout_read and not metrics.get("holdout_recorded", False):
        reasons.append("holdout_read_not_recorded")
    return ValidationResult(not reasons, tuple(reasons), adjusted, count, holdout_read, metrics)


class StrategyValidator:
    """Small object wrapper for callers that keep configured gate state."""

    def __init__(
        self,
        *,
        ledger: ExperimentLedger | None = None,
        config: Gate1Config = Gate1Config(),
    ) -> None:
        self.ledger = ledger
        self.config = config

    def validate(
        self, metrics: Mapping[str, Any], *, holdout_read: bool = False
    ) -> ValidationResult:
        return validate_gate1(
            metrics,
            ledger=self.ledger,
            config=self.config,
            holdout_read=holdout_read,
        )


validate = validate_gate1


__all__ = [
    "Gate1Config",
    "HoldoutAccessError",
    "HoldoutReader",
    "StrategyValidator",
    "ValidationResult",
    "bonferroni_alpha",
    "strategy_hash",
    "validate",
    "validate_gate1",
]
