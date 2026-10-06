"""Deterministic Strategy Lab gates and the sole sealed-holdout reader."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trader.lab.ledger import ExperimentLedgerProtocol


class HoldoutAccessError(PermissionError):
    """Raised when a holdout request is malformed or outside the sealed root."""


class HoldoutReader:
    """Read each strategy hash once and record the read in the experiment ledger."""

    def __init__(self, root: str | Path, *, ledger: Any = None, audit: Any = None) -> None:
        self._root = Path(root).resolve()
        self._ledger = ledger
        self._audit = audit
        self._read_hashes: set[str] = set()

    def read(self, spec_hash: str, path: str | Path) -> bytes:
        if not spec_hash.strip() or spec_hash in self._read_hashes:
            raise HoldoutAccessError("holdout is readable only once per spec hash")
        candidate = Path(path).resolve()
        if self._root not in candidate.parents and candidate != self._root:
            raise HoldoutAccessError("holdout path is outside the sealed root")
        if not candidate.is_file():
            raise HoldoutAccessError("sealed holdout file does not exist")
        content = candidate.read_bytes()
        self._read_hashes.add(spec_hash)
        if self._ledger is not None:
            from trader.lab.ledger import ExperimentRecord

            self._ledger.append(
                ExperimentRecord(
                    strategy_hash=spec_hash,
                    parameters={},
                    data_start=datetime.now(UTC),
                    data_end=datetime.now(UTC),
                    metrics={"holdout_bytes": len(content)},
                    holdout_accessed=True,
                )
            )
        if self._audit is not None:
            from trader.core.audit import AuditEvent

            self._audit.append(AuditEvent("holdout_read", {"strategy_hash": spec_hash}))
        return content


def strategy_hash(spec: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(spec), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bonferroni_alpha(alpha: float, trials: int) -> float:
    if not 0 < alpha < 1 or trials < 1:
        raise ValueError("alpha must be between zero and one and trials must be positive")
    return alpha / trials


@dataclass(frozen=True, slots=True)
class Gate1Config:
    min_trades: int
    min_out_of_sample_trades: int
    min_walk_forward_windows: int
    min_validation_periods: int
    min_out_of_sample_profit_factor: float
    min_holdout_profit_factor: float
    min_profitable_walk_forward_share: float
    cost_stress_increase_pct: float
    require_neighbor_parameters_profitable: bool
    require_multiple_testing_correction: bool
    min_profitable_regimes: int
    max_top5pct_trades_profit_share: float
    max_backtest_drawdown_pct: float
    alpha: float = 0.05

    @classmethod
    def from_config(cls, path: str | Path | None = None) -> Gate1Config:
        target = (
            Path(path)
            if path
            else Path(__file__).resolve().parents[3] / "config" / "strategy_lab.yaml"
        )
        try:
            import yaml

            payload = yaml.safe_load(target.read_text(encoding="utf-8"))
            gate = payload["gate1_backtest_to_paper"]
        except ImportError:
            gate = _minimal_yaml_gate(target)
        except (KeyError, TypeError, OSError) as exc:
            raise ValueError("strategy_lab.yaml gate1_backtest_to_paper is invalid") from exc
        expected = {
            "min_trades",
            "min_out_of_sample_trades",
            "min_walk_forward_windows",
            "min_validation_periods",
            "min_out_of_sample_profit_factor",
            "min_holdout_profit_factor",
            "min_profitable_walk_forward_share",
            "cost_stress_increase_pct",
            "require_neighbor_parameters_profitable",
            "require_multiple_testing_correction",
            "min_profitable_regimes",
            "max_top5pct_trades_profit_share",
            "max_backtest_drawdown_pct",
        }
        if set(gate) != expected:
            raise ValueError(
                "strategy_lab.yaml gate1_backtest_to_paper has missing or unknown keys"
            )
        return cls(**{key: gate[key] for key in expected})


def _minimal_yaml_gate(path: Path) -> dict[str, Any]:
    values: dict[str, Any] = {}
    active = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "gate1_backtest_to_paper:":
            active = True
            continue
        if active and line and not line.startswith(" "):
            break
        if active and ":" in line:
            key, raw = (part.strip() for part in line.split(":", 1))
            raw = raw.split("#", 1)[0].strip()
            values[key] = (
                raw.lower() == "true"
                if raw.lower() in {"true", "false"}
                else float(raw)
                if "." in raw
                else int(raw)
            )
    return values


@dataclass(frozen=True, slots=True)
class ValidationResult:
    passed: bool
    reasons: tuple[str, ...]
    adjusted_alpha: float
    trials: int
    holdout_read: bool = False
    metrics: Mapping[str, Any] = field(default_factory=dict)


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("gate metrics must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError("gate metrics must be finite")
    return number


def validate_gate1(
    metrics: Mapping[str, Any],
    *,
    ledger: ExperimentLedgerProtocol | None = None,
    trials: int | None = None,
    config: Gate1Config | None = None,
    holdout_spec_hash: str | None = None,
) -> ValidationResult:
    """Apply Gate 1, deriving trial count and holdout evidence internally."""
    configured = config or Gate1Config.from_config()
    ledger_count = ledger.count() if ledger is not None else 0
    if trials is not None and trials < ledger_count:
        raise ValueError("trials cannot be below the ledger count")
    count = max(1, trials if trials is not None else ledger_count)
    adjusted = bonferroni_alpha(configured.alpha, count)
    required = {
        "trades": (configured.min_trades, "too_few_trades"),
        "out_of_sample_trades": (
            configured.min_out_of_sample_trades,
            "too_few_out_of_sample_trades",
        ),
        "walk_forward_windows": (
            configured.min_walk_forward_windows,
            "too_few_walk_forward_windows",
        ),
        "validation_periods": (configured.min_validation_periods, "too_few_validation_periods"),
        "out_of_sample_profit_factor": (
            configured.min_out_of_sample_profit_factor,
            "out_of_sample_profit_factor_below_gate",
        ),
        "holdout_profit_factor": (
            configured.min_holdout_profit_factor,
            "holdout_profit_factor_below_gate",
        ),
        "profitable_walk_forward_share": (
            configured.min_profitable_walk_forward_share,
            "walk_forward_share_below_gate",
        ),
        "profitable_regimes": (configured.min_profitable_regimes, "too_few_profitable_regimes"),
    }
    reasons: list[str] = []
    for name, (minimum, reason) in required.items():
        value = metrics.get(name)
        if value is None or _number(value) < minimum:
            reasons.append(reason)
    drawdown = metrics.get("backtest_drawdown_pct")
    if drawdown is None or _number(drawdown) > configured.max_backtest_drawdown_pct:
        reasons.append("backtest_drawdown_above_gate")
    top_share = metrics.get("top5pct_trades_profit_share")
    if top_share is None or _number(top_share) > configured.max_top5pct_trades_profit_share:
        reasons.append("profit_concentration_above_gate")
    if (
        configured.require_neighbor_parameters_profitable
        and metrics.get("neighbor_parameters_profitable") is not True
    ):
        reasons.append("neighbor_parameters_not_profitable")
    p_value = metrics.get("p_value")
    if p_value is None or _number(p_value) > adjusted:
        reasons.append("adjusted_significance_failed")
    if metrics.get("cost_stress_net_positive") is not True:
        reasons.append("cost_stress_failed")
    if holdout_spec_hash is not None:
        seen = ledger is not None and any(
            item.strategy_hash == holdout_spec_hash and item.holdout_accessed
            for item in ledger.records()
        )
        if not seen:
            reasons.append("holdout_read_not_recorded")
    return ValidationResult(
        not reasons,
        tuple(reasons),
        adjusted,
        count,
        holdout_spec_hash is not None,
        metrics,
    )


class StrategyValidator:
    def __init__(
        self,
        *,
        ledger: ExperimentLedgerProtocol | None = None,
        config: Gate1Config | None = None,
    ) -> None:
        self.ledger = ledger
        self.config = config

    def validate(
        self,
        metrics: Mapping[str, Any],
        *,
        holdout_spec_hash: str | None = None,
    ) -> ValidationResult:
        return validate_gate1(
            metrics,
            ledger=self.ledger,
            config=self.config,
            holdout_spec_hash=holdout_spec_hash,
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
