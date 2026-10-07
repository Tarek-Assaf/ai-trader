"""Concrete runtime service assembly tests."""

from __future__ import annotations

from types import SimpleNamespace

from trader.runtime.services import build_runtime_services


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
    }
    settings = SimpleNamespace(database_url=None)
    result = build_runtime_services(settings, {}, overrides=values)
    assert result["agent_graph"] is sentinel
    assert result["broker"] is sentinel
