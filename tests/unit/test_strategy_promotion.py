from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from trader.lab.promotion import PromotionGate
from trader.lab.validator import ValidationResult


@dataclass
class _Audit:
    events: list[object]

    def append(self, event: object) -> None:
        self.events.append(event)


def _config() -> dict[str, object]:
    return yaml.safe_load(
        (Path(__file__).parents[2] / "config" / "strategy_lab.yaml").read_text(encoding="utf-8")
    )


def _gate(config: Mapping[str, object], name: str) -> dict[str, object]:
    return cast(dict[str, object], config[name])


def _metrics(gate: Mapping[str, object]) -> dict[str, object]:
    metrics: dict[str, object] = {}
    for key, expected in gate.items():
        if key.startswith("min_"):
            metrics[key.removeprefix("min_")] = expected
        elif key.startswith("require_"):
            metrics[key.removeprefix("require_")] = True
    metrics["cost_stress_net_positive"] = True
    metrics["risk_rule_violations"] = 0
    return metrics


def test_gate1_then_demo_gate2_uses_configured_thresholds_and_records_transition() -> None:
    audit = _Audit([])
    alerts: list[str] = []
    gate = PromotionGate(_config(), live_trading_enabled=True, audit=audit, alert=alerts.append)
    validation = ValidationResult(True, (), 0.05, 1, "s1", True)
    config = _config()

    gate1 = _gate(config, "gate1_backtest_to_paper")
    paper = gate.promote(
        "s1",
        "backtest",
        _metrics(gate1),
        target_stage="paper",
        validation=validation,
    )
    assert paper.approved and paper.stage == "paper"

    gate2 = _gate(config, "gate2_paper_to_micro_live")
    blocked_metrics = _metrics(gate2)
    blocked_metrics["weeks"] = 3
    blocked = gate.promote("s1", "paper", blocked_metrics, target_stage="micro_live")
    assert not blocked.approved and "min_weeks_failed" in blocked.reasons

    demo = gate.promote("s1", "paper", _metrics(gate2), target_stage="micro_live")
    assert demo.approved and demo.stage == "micro_live"
    assert demo.capital_cap == 20
    assert alerts == ["strategy_stage_changed:s1:paper", "strategy_stage_changed:s1:micro_live"]
    assert len(audit.events) == 2


def test_gate2_requires_all_real_config_requirements() -> None:
    config = _config()
    gate = PromotionGate(config, live_trading_enabled=True, audit=_Audit([]))
    gate2 = _gate(config, "gate2_paper_to_micro_live")
    weeks_and_trades_only = {"weeks": gate2["min_weeks"], "trades": gate2["min_trades"]}
    blocked = gate.promote("s1", "paper", weeks_and_trades_only, target_stage="micro_live")
    assert not blocked.approved
    assert "require_within_backtest_confidence_band_failed" in blocked.reasons
    assert "require_costs_match_model_failed" in blocked.reasons
    assert "risk_rule_violations" in blocked.reasons

    perfect = _metrics(gate2)
    assert gate.promote("s1", "paper", perfect, target_stage="micro_live").approved


def test_live_stages_fail_closed_without_flag_or_recorded_gate() -> None:
    disabled = PromotionGate(_config())
    decision = disabled.promote(
        "s1", "paper", {"weeks": 4, "trades": 200}, target_stage="micro_live"
    )
    assert not decision.approved and decision.reasons == ("live_trading_disabled",)

    unrecorded = PromotionGate(_config(), live_trading_enabled=True)
    decision = unrecorded.promote(
        "s1", "paper", {"weeks": 4, "trades": 200}, target_stage="micro_live"
    )
    assert not decision.approved and decision.reasons == ("stage_gate_not_recorded",)

    live_disabled = PromotionGate(_config(), live_trading_enabled=False, audit=_Audit([]))
    decision = live_disabled.promote(
        "s1", "micro_live", _metrics(_gate(_config(), "gate3_micro_to_live")), target_stage="live"
    )
    assert not decision.approved and decision.reasons == ("live_trading_disabled",)
