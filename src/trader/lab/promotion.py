"""Promotion stages, safety gates, audit events and owner alerts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from trader.lab.validator import ValidationResult

if TYPE_CHECKING:
    from trader.core.audit import AuditWriter

STAGES = ("idea", "backtest", "paper", "micro_live", "live", "retired")


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    strategy_id: str
    previous_stage: str
    stage: str
    approved: bool
    reasons: tuple[str, ...]
    capital_cap: Decimal


def _as_decimal(value: object, default: str = "0") -> Decimal:
    return Decimal(str(default if value is None else value))


class PromotionGate:
    """Apply configured stage gates; live stages stay unreachable by default."""

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        live_trading_enabled: bool = False,
        audit: AuditWriter | None = None,
        alert: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._live_enabled = live_trading_enabled
        self._audit = audit
        self._alert = alert

    def promote(
        self,
        strategy_id: str,
        current_stage: str,
        metrics: Mapping[str, object],
        *,
        target_stage: str | None = None,
        validation: ValidationResult | None = None,
    ) -> PromotionDecision:
        if current_stage not in STAGES:
            raise ValueError("unknown strategy stage")
        target = target_stage or self._next_stage(current_stage)
        if target not in STAGES:
            raise ValueError("unknown strategy stage")
        reasons = self._reasons(current_stage, target, metrics, validation)
        approved = not reasons
        cap = self._capital_cap(target)
        decision = PromotionDecision(
            strategy_id,
            current_stage,
            target if approved else current_stage,
            approved,
            tuple(reasons),
            cap,
        )
        if approved and target != current_stage:
            self._record(decision)
        return decision

    def _next_stage(self, stage: str) -> str:
        return STAGES[min(STAGES.index(stage) + 1, len(STAGES) - 1)]

    def _capital_cap(self, stage: str) -> Decimal:
        values = self._config.get("stage_capital", {})
        if not isinstance(values, Mapping):
            return Decimal("0")
        if stage == "micro_live":
            return _as_decimal(values.get("micro_live_size_pct_of_target"))
        if stage == "live":
            ramp = values.get("live_ramp_pct", [])
            return _as_decimal(max(ramp) if ramp else 0)
        return Decimal("0")

    def _reasons(
        self,
        current: str,
        target: str,
        metrics: Mapping[str, object],
        validation: ValidationResult | None,
    ) -> list[str]:
        if STAGES.index(target) != STAGES.index(current) + 1:
            return ["stage_transition_must_be_adjacent"]
        if target in {"micro_live", "live"} and not self._live_enabled:
            return ["live_trading_disabled"]
        gate_name = {
            "paper": "gate1_backtest_to_paper",
            "micro_live": "gate2_paper_to_micro_live",
            "live": "gate3_micro_to_live",
        }.get(target, "")
        gate = self._config.get(gate_name, {})
        if not isinstance(gate, Mapping) or not gate:
            return ["gate_config_missing"]
        allowed = {
            "min_weeks", "min_trades", "min_out_of_sample_trades", "min_validation_periods",
            "min_walk_forward_windows", "min_out_of_sample_profit_factor",
            "min_holdout_profit_factor", "max_risk_rule_violations",
            "max_drawdown_vs_backtest_ratio", "slippage_tolerance_vs_paper_pct",
            "require_within_backtest_confidence_band", "require_costs_match_model",
            "require_neighbor_parameters_profitable", "require_multiple_testing_correction",
            "ramp_min_trades_per_step", "cost_stress_increase_pct",
            "min_profitable_walk_forward_share", "max_top5pct_trades_profit_share",
            "max_backtest_drawdown_pct", "min_profitable_regimes",
        }
        unknown = set(gate) - allowed
        if unknown:
            return ["unknown_gate_keys"]
        reasons: list[str] = []
        if target == "paper" and (
            validation is None
            or not validation.passed
            or validation.metrics.get("strategy_hash") != metrics.get("strategy_hash")
        ):
            reasons.append("validation_result_missing_or_mismatched")
        for key, expected in gate.items():
            if not key.startswith("min_"):
                continue
            actual = metrics.get(key.removeprefix("min_"))
            if actual is None or _as_decimal(actual) < _as_decimal(expected):
                reasons.append(f"{key}_failed")
        if (
            target == "micro_live"
            and int(metrics.get("risk_rule_violations", 1))
            > int(gate.get("max_risk_rule_violations", 0))
        ):
            reasons.append("risk_rule_violations")
        if target == "live" and _as_decimal(
            metrics.get("max_drawdown_vs_backtest_ratio")
        ) > _as_decimal(gate.get("max_drawdown_vs_backtest_ratio", 1)):
            reasons.append("drawdown_ratio")
        for key in ("require_within_backtest_confidence_band", "require_costs_match_model"):
            if gate.get(key) is True and metrics.get(key.removeprefix("require_")) is not True:
                reasons.append(f"{key}_failed")
        for key in (
            "require_neighbor_parameters_profitable",
            "require_multiple_testing_correction",
        ):
            if gate.get(key) is True and metrics.get(key.removeprefix("require_")) is not True:
                reasons.append(f"{key}_failed")
        if (
            "cost_stress_increase_pct" in gate
            and metrics.get("cost_stress_net_positive") is not True
        ):
            reasons.append("cost_stress_failed")
        if target == "live" and "ramp_min_trades_per_step" in gate:
            if _as_decimal(metrics.get("trades_since_ramp", 0)) < _as_decimal(
                gate["ramp_min_trades_per_step"]
            ):
                reasons.append("ramp_min_trades_per_step_failed")
        if target == "live" and "slippage_tolerance_vs_paper_pct" in gate:
            if _as_decimal(metrics.get("slippage_vs_paper_pct")) > _as_decimal(
                gate["slippage_tolerance_vs_paper_pct"]
            ):
                reasons.append("slippage_tolerance_failed")
        return reasons

    def _record(self, decision: PromotionDecision) -> None:
        if self._audit is not None:
            from trader.core.audit import AuditEvent

            self._audit.append(
                AuditEvent(
                    "strategy_stage_changed",
                    {
                        "strategy_id": decision.strategy_id,
                        "from": decision.previous_stage,
                        "to": decision.stage,
                        "capital_cap": str(decision.capital_cap),
                    },
                )
            )
        if self._alert is not None:
            self._alert(f"strategy_stage_changed:{decision.strategy_id}:{decision.stage}")


__all__ = ["PromotionDecision", "PromotionGate", "STAGES"]
