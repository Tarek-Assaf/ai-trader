from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trader.reports.commands import CommandDataService
from trader.soak import SoakMonitor


def test_daily_metrics_and_summary_reset_clock_on_incident(tmp_path) -> None:
    current = [datetime(2026, 10, 7, 12, tzinfo=UTC)]
    alerts: list[str] = []
    monitor = SoakMonitor(tmp_path, clock=lambda: current[0], alert=alerts.append)
    monitor.record_cycle()
    monitor.record_intent()
    monitor.record_guardian_rejection("daily_loss")
    monitor.record_order()
    monitor.record_fill()
    monitor.record_llm_spend(Decimal("1.25"))
    monitor.record_equity(Decimal("999"), drawdown_pct=Decimal("1.0"))
    assert monitor.summary()["days_run"] == 1
    current[0] += timedelta(minutes=1)
    incidents = monitor.evaluate({"daily_loss_pct": "3", "daily_loss_limit_pct": "2"})
    assert [item.rule for item in incidents] == ["daily_loss_limit_exceeded"]
    assert alerts == ["daily_loss_limit_exceeded"]
    assert monitor.summary()["postmortem_required"] is True
    evidence = list(tmp_path.glob("incident-*.json"))
    assert len(evidence) == 1
    assert json.loads(evidence[0].read_text(encoding="utf-8"))["rule"] == (
        "daily_loss_limit_exceeded"
    )


def test_all_incident_rules_emit_secret_free_evidence(tmp_path) -> None:
    monitor = SoakMonitor(tmp_path, secret_patterns=("owner-token",))
    incidents = monitor.evaluate(
        {
            "order_without_server_side_stop": True,
            "open_position_without_broker_stop": True,
            "reconciliation_mismatch_age_seconds": 301,
            "risk_per_trade_pct": 1,
            "risk_per_trade_limit_pct": "0.5",
            "daily_loss_pct": 3,
            "daily_loss_limit_pct": 2,
            "control_requested": "kill",
            "control_honored": False,
            "log_text": "owner-token",
        }
    )
    assert {item.rule for item in incidents} == set(SoakMonitor.RULES)
    for path in tmp_path.glob("incident-*.json"):
        text = path.read_text(encoding="utf-8")
        assert "owner-token" not in text


def test_repeated_rule_does_not_create_evidence_storm(tmp_path) -> None:
    monitor = SoakMonitor(tmp_path)
    observation = {"order_without_server_side_stop": True}
    assert len(monitor.evaluate(observation)) == 1
    assert len(monitor.evaluate(observation)) == 0
    assert monitor.summary_command().startswith("Soak: 1 days run;")


def test_soak_command_is_available_without_the_llm(tmp_path) -> None:
    monitor = SoakMonitor(tmp_path)
    service = CommandDataService(soak_provider=lambda: monitor)
    assert service.handlers()["/soak"]("/soak").startswith("Soak:")
