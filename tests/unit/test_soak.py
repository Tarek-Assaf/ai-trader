from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trader.execution.control import ControlMode
from trader.reports.commands import CommandDataService
from trader.risk.guardian import (
    AccountState,
    MarketSnapshot,
    RiskGuardian,
    RiskLimits,
    SizingEstimate,
    TradeIntent,
)
from trader.runtime.composition import RuntimeComponents, RuntimeResult, RuntimeService
from trader.runtime.soak import LogScanner, RuntimeSoakFeed
from trader.soak import SoakMonitor


class _Journal:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict[str, object]] = {}

    def get_snapshot(self, snapshot_id: str) -> dict[str, object] | None:
        return self.snapshots.get(snapshot_id)

    def save_snapshot(self, snapshot_id: str, payload: dict[str, object]) -> None:
        self.snapshots[snapshot_id] = payload


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


def test_state_and_four_week_clock_survive_restart(tmp_path) -> None:
    current = [datetime(2026, 10, 7, 12, tzinfo=UTC)]
    journal = _Journal()
    first = SoakMonitor(tmp_path, clock=lambda: current[0], journal=journal)
    first.record_cycle()
    first.observe_cycle(status="rejected", reason="guardian_rejected")
    first.evaluate({"daily_loss_pct": 3, "daily_loss_limit_pct": 2})
    started = first.clock_started_at

    current[0] += timedelta(days=1)
    second = SoakMonitor(tmp_path, clock=lambda: current[0], journal=journal)
    summary = second.summary()
    assert summary["days_run"] == 1
    assert summary["incidents"] == 1
    assert summary["days"][0]["cycles"] == 1
    assert summary["days"][0]["intents"] == 1
    assert summary["days"][0]["guardian_rejections"] == {"guardian_rejected": 1}
    assert second.clock_started_at == started
    assert second.summary_command().startswith("Soak: 1 days run; 1 incidents;")


def test_runtime_feed_collects_real_safety_component_observations(tmp_path) -> None:
    class Source:
        def __init__(self, values):
            self.values = values

        def soak_observation(self):
            return self.values

    feed = RuntimeSoakFeed(
        {
            "guardian": Source({"risk_per_trade_pct": "1", "risk_per_trade_limit_pct": "0.5"}),
            "position_manager": Source({"open_position_without_broker_stop": True}),
            "order_manager": Source({"order_without_server_side_stop": True}),
            "pnl_guard": Source({"daily_loss_pct": "3", "daily_loss_limit_pct": "2"}),
            "reconciler": Source({"reconciliation_mismatch_age_seconds": 301}),
            "control": Source({"control_requested": "kill", "control_honored": False}),
            "log_scan": Source({"log_text": "owner-token"}),
        }
    )
    monitor = SoakMonitor(tmp_path, secret_patterns=("owner-token",))
    incidents = monitor.evaluate(feed.collect())
    assert {item.rule for item in incidents} == set(SoakMonitor.RULES)
    assert all(
        "owner-token" not in path.read_text(encoding="utf-8")
        for path in tmp_path.glob("incident-*.json")
    )


def test_log_scanner_detects_secret_without_returning_safe_logs(tmp_path) -> None:
    path = tmp_path / "app.log"
    path.write_text("startup owner-token", encoding="utf-8")
    scanner = LogScanner(tmp_path, secret_patterns=("owner-token",))
    assert scanner.soak_observation() == {"log_text": "startup owner-token"}


def test_missing_soak_source_warns_once_and_skips_only_its_rule(caplog) -> None:
    class Pnl:
        def soak_observation(self):
            return {"daily_loss_pct": 3, "daily_loss_limit_pct": 2}

    feed = RuntimeSoakFeed({"order_manager": None, "pnl_guard": Pnl()})
    with caplog.at_level("WARNING"):
        first = feed.collect()
        second = feed.collect()
    assert first == second == {"daily_loss_pct": 3, "daily_loss_limit_pct": 2}
    assert [record.message for record in caplog.records].count(
        "soak observation source unavailable: order_manager"
    ) == 1


def test_real_guardian_observation_is_read_only() -> None:
    class Audit:
        def append(self, _event):
            return None

    limits = RiskLimits(
        risk_per_trade_pct=Decimal("1"),
        max_daily_loss_pct=Decimal("2"),
        max_weekly_loss_pct=Decimal("5"),
        max_drawdown_pct=Decimal("10"),
        max_open_positions_total=3,
        max_open_positions_per_instrument=2,
        max_combined_risk_pct=Decimal("3"),
        max_margin_usage_pct=Decimal("50"),
        max_effective_leverage=Decimal("5"),
        min_reward_risk=Decimal("1"),
        min_stop_atr=Decimal("0.5"),
        max_stop_atr=Decimal("3"),
        max_spread_atr_ratio=Decimal("2"),
        stale_data_max_seconds=180,
        max_orders_per_hour=20,
        blackout_before_min=30,
        blackout_after_min=30,
        consecutive_loss_pause=3,
        loss_cooldown_minutes=15,
        min_trade_quality_score=Decimal("0.5"),
    )
    guardian = RiskGuardian(limits, Audit(), canonical_symbols=("NAS100",))
    account = AccountState(
        equity=Decimal("1000"),
        day_start_equity=Decimal("1000"),
        week_start_equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        margin_used=Decimal("0"),
        notional_exposure=Decimal("0"),
        orders_last_hour=0,
        consecutive_losses=0,
        last_loss_at=None,
    )
    market = MarketSnapshot(
        datetime(2026, 10, 7, tzinfo=UTC),
        True,
        False,
        Decimal("100"),
        Decimal("100.1"),
        Decimal("1"),
    )
    sizing = SizingEstimate(
        size=Decimal("1"),
        risk_amount=Decimal("20"),
        margin_required=Decimal("10"),
        notional_exposure=Decimal("20"),
    )
    guardian.evaluate(
        TradeIntent("NAS100", "buy", Decimal("100"), Decimal("99"), Decimal("102")),
        market,
        account,
        (),
        sizing,
        stage_size_cap=None,
        trade_quality_score=Decimal("1"),
        now=datetime(2026, 10, 7, tzinfo=UTC),
        cycle_id="soak",
    )
    observed = guardian.soak_observation()
    assert Decimal(str(observed["risk_per_trade_pct"])) == Decimal("2")
    assert Decimal(str(observed["risk_per_trade_limit_pct"])) == Decimal("1")


def test_runtime_service_feeds_breach_alert_evidence_and_resets_clock(tmp_path) -> None:
    class Lease:
        def acquire(self):
            return None

        def check(self):
            return None

        def release(self):
            return None

    class Control:
        def read(self):
            return ControlMode.RUN

    class Loop:
        def run(self, _cycle):
            return RuntimeResult("NO_TRADE", "ok", value={})

    class Source:
        def soak_observation(self):
            return {"order_without_server_side_stop": True}

    current = [datetime(2026, 10, 7, 12, tzinfo=UTC)]
    alerts: list[str] = []
    monitor = SoakMonitor(tmp_path, clock=lambda: current[0], alert=alerts.append)
    components = RuntimeComponents(
        leader=Lease(), decision_loop=Loop(), order_manager=Source(), soak_monitor=monitor
    )
    runtime = RuntimeService(components, control_source=Control(), alert=alerts.append)
    import asyncio

    async def run():
        await runtime.start()
        await runtime.run_cycle(object())
        await runtime.stop()

    asyncio.run(run())
    assert alerts == ["order_without_server_side_stop"]
    assert monitor.clock_started_at == current[0]
    assert len(list(tmp_path.glob("incident-*.json"))) == 1
    assert "owner-token" not in next(tmp_path.glob("incident-*.json")).read_text()
