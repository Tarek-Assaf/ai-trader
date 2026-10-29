"""Concrete runtime service assembly tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from pydantic import SecretStr

from trader.core.config import load_all_configs
from trader.runtime.services import build_runtime_services

ROOT = Path(__file__).resolve().parents[2]


def test_service_bundle_accepts_fixture_dependencies_and_graph() -> None:
    sentinel = object()
    values = {
        "engine": sentinel,
        "audit_writer": sentinel,
        "broker": sentinel,
        "guardian": sentinel,
        "order_manager": sentinel,
        "reconciler": sentinel,
        "report_scheduler": sentinel,
        "settings_service": sentinel,
        "agent_graph_builder": lambda *_args: sentinel,
        "control_source": sentinel,
        "control_writer": sentinel,
        "redis_client": sentinel,
    }
    settings = SimpleNamespace(database_url=None)
    result = build_runtime_services(settings, {}, overrides=values)
    assert result["agent_graph"] is sentinel
    assert result["broker"] is sentinel


class _Redis:
    def set(self, *_args: object, **_kwargs: object) -> bool:
        return True

    def eval(self, *_args: object) -> int:
        return 1


def test_no_service_overrides_builds_real_paper_services() -> None:
    configs = load_all_configs(ROOT / "config")
    settings = SimpleNamespace(
        redis_url=SecretStr("redis://unused"),
        database_url=SecretStr("sqlite://"),
    )
    result = build_runtime_services(
        settings,
        configs,
        overrides={
            "redis_client": _Redis(),
            "control_source": object(),
            "control_writer": object(),
        },
    )
    assert result["order_manager"].__class__.__name__ == "OrderManager"
    assert result["broker"].__class__.__name__ == "PaperBroker"
    assert result["broker"].get_account()["balance"] == Decimal("10000")
    assert result["broker"].get_market_details("US100")["spread_points"] == Decimal("2.0")
    assert result["guardian_context_provider"].__class__.__name__ == "RuntimeContextProvider"


def test_missing_control_channel_keeps_runtime_stopped() -> None:
    import pytest

    from trader.runtime import RuntimeDependencyUnavailable

    settings = SimpleNamespace(
        redis_url=SecretStr("redis://unused"), database_url=SecretStr("sqlite://")
    )
    with pytest.raises(RuntimeDependencyUnavailable):
        build_runtime_services(settings, {}, overrides={"redis_client": _Redis()})


def test_report_scheduler_persists_each_period_once_per_scheduled_target() -> None:
    from test_reports import _snapshot

    from trader.runtime.services import _ReportScheduler

    class Store:
        def __init__(self) -> None:
            self.value = None

        def get_snapshot(self, _key: str) -> object:
            return self.value

        def save_snapshot(self, _key: str, value: object) -> None:
            self.value = value

    schedule = {
        "reports": {
            "daily": {"at": "09:00"},
            "weekly": {"day": "wednesday", "at": "09:30"},
            "monthly": {"day_of_month": 7, "at": "10:00"},
        }
    }
    store = Store()
    calls: list[str] = []
    scheduler = _ReportScheduler(
        schedule,
        snapshot_provider=lambda period: calls.append(period) or _snapshot(),
        state_store=store,
    )
    now = datetime(2026, 10, 7, 11, tzinfo=UTC)
    assert set(scheduler.run_due(now)) == {"daily", "weekly", "monthly"}
    assert len(calls) == 3
    assert scheduler.run_due(now) == ()
    assert len(calls) == 3


def test_report_scheduler_startup_line_lists_periods_and_next_target() -> None:
    from trader.runtime.services import _ReportScheduler

    scheduler = _ReportScheduler(
        {
            "reports": {
                "daily": {"at": "09:00"},
                "weekly": {"day": "wednesday", "at": "09:30"},
                "monthly": {"day_of_month": 7, "at": "10:00"},
            }
        },
        clock=lambda: datetime(2026, 10, 7, 8, 0, tzinfo=UTC),
    )

    line = scheduler.startup_schedule_line()
    assert line.startswith("reports scheduled daily/weekly/monthly next=")
    assert "2026-10-07T09:00:00+00:00" in line


def test_recorder_health_digest_runs_at_thresholds_and_repeats(tmp_path: Path) -> None:
    from trader.runtime.services import _ReportScheduler

    started = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
    clock = [started]
    status_path = tmp_path / "recorder.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "healthy",
                "ping_reply_ok": True,
                "market_status": "OPEN",
                "expected_open": True,
                "ticks_received": 11,
                "last_tick_at": started.isoformat(),
                "last_closed_bar_at": started.isoformat(),
                "reconnect_count": 0,
                "last_reconnect_reason": None,
                "rejected_candles": 0,
                "last_reject_reason": None,
                "stale_epics": [],
            }
        ),
        encoding="utf-8",
    )
    recorder = SimpleNamespace(_started_at=started, _status_path=status_path)
    sent: list[object] = []
    evidence = tmp_path / "evidence" / "recorder_health.jsonl"
    scheduler = _ReportScheduler(
        {"recorder_health": {"digest_hours": [2, 24], "repeat_hours": 24}},
        recorder=recorder,
        evidence_path=evidence,
        clock=lambda: clock[0],
        alert=lambda value: sent.append(value) or True,
    )

    assert scheduler.run_due(started) == ()
    clock[0] = started.replace(hour=2)
    assert scheduler.run_due() == ("recorder_health_2h",)
    clock[0] = started.replace(day=9)
    assert scheduler.run_due() == ("recorder_health_24h",)
    clock[0] = started.replace(day=10)
    assert scheduler.run_due() == ("recorder_health_48h",)
    lines = [json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines()]
    assert [line["uptime_label"] for line in lines] == ["2h", "24h", "48h"]
    assert all("token" not in line["message"].lower() for line in lines)
    assert len(sent) == 3


def test_recorder_digest_uses_shared_live_status_provider(tmp_path: Path) -> None:
    """The digest follows the same live status source as /health."""
    from trader.runtime.services import _ReportScheduler

    started = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
    stale_status_path = tmp_path / "api-recorder.json"
    stale_status_path.write_text(json.dumps({"status": "starting", "ticks_received": 0}))
    live_status = {
        "configured": True,
        "status": "healthy",
        "ping_reply_ok": True,
        "market_status": "TRADEABLE",
        "expected_open": True,
        "ticks_received": 720,
        "last_tick_at": started.isoformat(),
        "last_closed_bar_at": started.isoformat(),
        "reconnect_count": 2,
        "last_reconnect_reason": "ConnectionClosedError",
        "rejected_candles": 0,
        "last_reject_reason": None,
        "stale_epics": [],
    }
    recorder = SimpleNamespace(_started_at=started, _status_path=stale_status_path)
    evidence = tmp_path / "evidence" / "recorder_health.jsonl"
    scheduler = _ReportScheduler(
        {"recorder_health": {"digest_hours": [2]}},
        recorder=recorder,
        recorder_status_provider=lambda: live_status,
        evidence_path=evidence,
        clock=lambda: started.replace(hour=2),
    )

    assert scheduler.run_due() == ("recorder_health_2h",)
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["data_recorder"]["status"] == "healthy"
    assert payload["data_recorder"]["ticks_received"] == 720
    assert payload["data_recorder"]["market_status"] == "TRADEABLE"


def test_recorder_digest_marks_unreadable_shared_status_unavailable(tmp_path: Path) -> None:
    """An unreadable live source must not fall back to stale starting state."""
    from trader.runtime.services import _ReportScheduler

    started = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
    stale_status_path = tmp_path / "api-recorder.json"
    stale_status_path.write_text(json.dumps({"status": "starting", "ticks_received": 0}))
    recorder = SimpleNamespace(_started_at=started, _status_path=stale_status_path)
    evidence = tmp_path / "evidence" / "recorder_health.jsonl"
    scheduler = _ReportScheduler(
        {"recorder_health": {"digest_hours": [2]}},
        recorder=recorder,
        recorder_status_provider=lambda: None,
        evidence_path=evidence,
        clock=lambda: started.replace(hour=2),
    )

    assert scheduler.run_due() == ("recorder_health_2h",)
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["data_recorder"]["status"] == "unavailable"
    assert payload["data_recorder"]["ticks_received"] == 0


def test_recorder_health_alerts_after_degradation_and_recovers(tmp_path: Path) -> None:
    from trader.runtime.services import _ReportScheduler

    started = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
    clock = [started]
    status_path = tmp_path / "recorder.json"
    status = {
        "status": "degraded",
        "ping_reply_ok": False,
        "market_status": "OPEN",
        "expected_open": True,
        "last_reconnect_reason": "token=https://secret.example",
    }
    status_path.write_text(json.dumps(status), encoding="utf-8")
    recorder = SimpleNamespace(_started_at=started, _status_path=status_path)
    sent: list[object] = []
    scheduler = _ReportScheduler(
        {"recorder_health": {"digest_hours": [1000], "degraded_minutes": 5}},
        recorder=recorder,
        evidence_path=tmp_path / "evidence.jsonl",
        clock=lambda: clock[0],
        alert=lambda value: sent.append(value) or True,
    )

    scheduler.run_due(started)
    clock[0] = started.replace(minute=5)
    scheduler.run_due()
    assert len(sent) == 1
    assert "https://" not in str(getattr(sent[0], "text", ""))
    status["status"] = "healthy"
    status["ping_reply_ok"] = True
    status_path.write_text(json.dumps(status), encoding="utf-8")
    clock[0] = started.replace(minute=6)
    scheduler.run_due()
    assert len(sent) == 2
    assert sent[1].alert_key == "recorder_health_recovered"


def test_open_positions_use_the_configured_symbol_not_the_broker_epic() -> None:
    from decimal import Decimal

    from trader.risk.guardian import OpenPosition
    from trader.runtime.services import _canonical_positions

    instruments = {
        "instruments": {
            "NAS100": {"capital_epic": "US100", "aliases": ["NAS100", "US100"]},
            "XAUUSD": {"capital_epic": "GOLD", "aliases": ["XAUUSD", "GOLD"]},
        }
    }
    raw = (
        OpenPosition("US100", Decimal("5"), Decimal("100"), Decimal("1000")),
        OpenPosition("GOLD", Decimal("3"), Decimal("50"), Decimal("500")),
        OpenPosition("OTHER", Decimal("1"), Decimal("1"), Decimal("1")),
    )

    names = [p.instrument for p in _canonical_positions(lambda: raw, instruments)()]

    assert names == ["NAS100", "XAUUSD", "OTHER"]
