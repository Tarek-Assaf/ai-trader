"""Production runtime factory and feed adapter tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from trader.core.config import load_all_configs
from trader.runtime import RuntimeFactories, RuntimeFeedAdapters, ScheduleCycleSource
from trader.runtime.factory import (
    OrderManagerDecisionLoop,
    build_default_factories,
    build_runtime_components,
)

ROOT = Path(__file__).resolve().parents[2]


class FakeRedis:
    def set(self, _name: str, _value: str, *, nx: bool, px: int) -> bool:
        assert nx is True and px > 0
        return True

    def eval(self, _script: str, _numkeys: int, *_args: str | int) -> int:
        return 1


def settings(**overrides: object) -> SimpleNamespace:
    values = {
        "role": "core",
        "trading_mode": "paper",
        "broker_env": "demo",
        "live_trading_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def factories() -> RuntimeFactories:
    return RuntimeFactories(
        redis_client=lambda _settings: FakeRedis(),
        broker=lambda _settings, _configs: object(),
        guardian=lambda _settings, _configs: object(),
        order_manager=lambda *_args: object(),
        reconciler=lambda *_args: object(),
        report_scheduler=lambda _settings, _configs: object(),
        settings_service=lambda _settings, _configs: object(),
        decision_loop=lambda *_args: object(),
    )


def test_factory_builds_complete_demo_components() -> None:
    components = build_runtime_components(settings(), {}, SimpleNamespace(), factories())
    assert components.missing_required() == ()
    assert components.metadata["broker_env"] == "demo"


def test_factory_rejects_live_or_incomplete_redis_composition() -> None:
    with pytest.raises(RuntimeError, match="demo core"):
        build_runtime_components(settings(broker_env="live"), {}, SimpleNamespace(), factories())
    bad = factories()
    object.__setattr__(bad, "redis_client", lambda _settings: object())
    with pytest.raises(TypeError, match="redis client"):
        build_runtime_components(settings(), {}, SimpleNamespace(), bad)


def test_feed_adapters_emit_stable_ids_and_alert_missing_feed() -> None:
    source = ScheduleCycleSource()
    alerts: list[str] = []
    feeds = RuntimeFeedAdapters(source, alert=alerts.append)
    at = datetime(2026, 1, 1, tzinfo=UTC)
    feeds.on_bar_close("1m", "42", at)
    feeds.on_session_open("london", at)
    feeds.on_news_event("cpi-2026-01", at)
    feeds.missing_feed("calendar")
    assert [source.next_cycle().event_id for _ in range(3)] == [
        "bar:1M:42",
        "session:london",
        "news:cpi-2026-01",
    ]
    assert alerts == ["runtime_feed_unavailable:calendar"]


def test_closed_bar_forwarding_uses_bar_open_boundaries() -> None:
    source = ScheduleCycleSource()
    feeds = RuntimeFeedAdapters(source)
    for minute in (2, 3, 4, 5):
        feeds.on_closed_bar(f"bar-{minute}", datetime(2026, 1, 1, 0, minute, tzinfo=UTC))
    ids = []
    while source.pending:
        ids.append(source.next_cycle().event_id)
    assert ids == [
        "bar:1M:bar-2",
        "bar:3M:bar-2",
        "bar:1M:bar-3",
        "bar:1M:bar-4",
        "bar:5M:bar-4",
        "bar:1M:bar-5",
        "bar:3M:bar-5",
    ]


def test_default_factories_build_paper_broker_and_audit_cycle() -> None:
    audit_rows: list[object] = []
    services = {
        "guardian": object(),
        "order_manager": object(),
        "reconciler": object(),
        "report_scheduler": object(),
        "settings_service": object(),
        "redis_client": lambda _settings: FakeRedis(),
        "audit_writer": SimpleNamespace(append=audit_rows.append),
    }
    configured = load_all_configs(ROOT / "config")
    built = build_default_factories(settings(), configured, services=services)
    components = build_runtime_components(settings(), configured, SimpleNamespace(), built)
    assert components.missing_required() == ()
    assert components.broker.get_account()["balance"] == Decimal("10000")
    assert components.broker.get_market_details("US100")["spread_points"] == Decimal("2.0")
    loop = OrderManagerDecisionLoop(object(), audit_writer=services["audit_writer"])
    cycle = SimpleNamespace(event_id="cycle-1")
    result = loop.run(cycle)
    assert result.action == "NO_TRADE"
    assert audit_rows[0].cycle_id == "cycle-1"
