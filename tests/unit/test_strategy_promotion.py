from __future__ import annotations

from dataclasses import dataclass

from trader.lab.promotion import PromotionGate
from trader.lab.validator import ValidationResult


@dataclass
class _Audit:
    events: list[object]

    def append(self, event: object) -> None:
        self.events.append(event)


def _config() -> dict[str, object]:
    return {
        "gate1_backtest_to_paper": {"min_trades": 300},
        "gate2_paper_to_micro_live": {"min_weeks": 4, "min_trades": 200},
        "gate3_micro_to_live": {"min_weeks": 4, "min_trades": 100},
        "stage_capital": {"micro_live_size_pct_of_target": 20, "live_ramp_pct": [25, 50, 100]},
    }


def test_gate1_then_demo_gate2_uses_configured_thresholds_and_records_transition() -> None:
    audit = _Audit([])
    alerts: list[str] = []
    gate = PromotionGate(_config(), live_trading_enabled=True, audit=audit, alert=alerts.append)
    validation = ValidationResult(True, (), 0.05, 1, "s1", True)

    paper = gate.promote(
        "s1",
        "backtest",
        {"trades": 300},
        target_stage="paper",
        validation=validation,
    )
    assert paper.approved and paper.stage == "paper"

    blocked = gate.promote(
        "s1", "paper", {"weeks": 3, "trades": 200}, target_stage="micro_live"
    )
    assert not blocked.approved and "min_weeks_failed" in blocked.reasons

    demo = gate.promote(
        "s1", "paper", {"weeks": 4, "trades": 200}, target_stage="micro_live"
    )
    assert demo.approved and demo.stage == "micro_live"
    assert demo.capital_cap == 20
    assert alerts == ["strategy_stage_changed:s1:paper", "strategy_stage_changed:s1:micro_live"]
    assert len(audit.events) == 2


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

