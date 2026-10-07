"""Production runtime factory and feed adapter tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from trader.runtime import RuntimeFactories, RuntimeFeedAdapters, ScheduleCycleSource
from trader.runtime.factory import build_runtime_components


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
