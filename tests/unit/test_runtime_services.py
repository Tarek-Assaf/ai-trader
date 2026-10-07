"""Concrete runtime service assembly tests."""

from __future__ import annotations

from types import SimpleNamespace

import yaml
from pydantic import SecretStr

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
    from pathlib import Path

    root = Path("config")
    risk = yaml.safe_load((root / "risk.yaml").read_text(encoding="utf-8"))
    ceilings = yaml.safe_load((root / "risk_ceilings.yaml").read_text(encoding="utf-8"))
    instruments = yaml.safe_load((root / "instruments.yaml").read_text(encoding="utf-8"))
    settings = SimpleNamespace(
        redis_url=SecretStr("redis://unused"),
        database_url=SecretStr("sqlite://"),
    )
    configs = {
        "risk": risk,
        "risk_ceilings": ceilings,
        "instruments": instruments,
        "paper_execution": {
            "starting_cash": "10000",
            "currency": "USD",
            "spread_points": {"NAS100": "1"},
            "slippage_points": {"NAS100": "0"},
        },
        "schedule": {},
    }
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


def test_missing_control_channel_keeps_runtime_stopped() -> None:
    import pytest

    from trader.runtime import RuntimeDependencyUnavailable

    settings = SimpleNamespace(
        redis_url=SecretStr("redis://unused"), database_url=SecretStr("sqlite://")
    )
    with pytest.raises(RuntimeDependencyUnavailable):
        build_runtime_services(settings, {}, overrides={"redis_client": _Redis()})
