"""Concrete runtime service assembly tests."""

from __future__ import annotations

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
