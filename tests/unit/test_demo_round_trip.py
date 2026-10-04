from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import scripts.demo_round_trip as round_trip
import pytest


class FakeClient:
    environment = "demo"

    def __init__(self) -> None:
        self.position_rows: list[dict[str, Any]] = []
        self.references: list[tuple[str, str]] = []

    def search_markets(self, term: str) -> list[dict[str, object]]:
        assert term == "US100"
        return [{"epic": "US100", "tickSize": 0.1}]

    def market_details(self, epic: str) -> dict[str, object]:
        assert epic == "US100"
        return {
            "instrument": {
                "epic": epic,
                "lotSize": 1,
                "marginFactor": 5,
                "marginFactorUnit": "PERCENTAGE",
                "currency": "USD",
                "tickSize": 0.1,
            },
            "dealingRules": {
                "minDealSize": {"value": 0.1, "unit": "POINTS"},
                "maxDealSize": {"value": 100, "unit": "POINTS"},
                "minSizeIncrement": {"value": 0.1, "unit": "POINTS"},
                "minStepDistance": {"value": 2, "unit": "POINTS"},
            },
            "snapshot": {"marketStatus": "TRADEABLE", "offer": 20000.0},
        }

    def get_positions(self) -> list[dict[str, Any]]:
        return self.position_rows

    def get_working_orders(self) -> list[dict[str, Any]]:
        return []

    def account_info(self) -> dict[str, object]:
        return {"accounts": []}

    def get_activity(self, start: object, end: object) -> list[dict[str, object]]:
        return []

    def open_position(self, payload: object) -> dict[str, str]:
        self.references.append(("open", "open-ref"))
        return {"dealReference": "open-ref"}

    def confirm_deal(self, reference: str) -> dict[str, str]:
        return {"dealStatus": "ACCEPTED"}

    def modify_position(self, deal_id: str, payload: object) -> dict[str, str]:
        self.references.append(("modify", "modify-ref"))
        return {"dealReference": "modify-ref"}

    def close_position(self, deal_id: str) -> dict[str, str]:
        self.references.append(("close", "close-ref"))
        return {"dealReference": "close-ref"}

    def cancel_working_order(self, deal_id: str) -> dict[str, str]:
        self.references.append(("cancel", "cancel-ref"))
        return {"dealReference": "cancel-ref"}


def test_environment_guard_requires_all_three_explicit_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in (
        ("BROKER_ENV", "demo"),
        ("TRADING_MODE", "paper"),
        ("LIVE_TRADING_ENABLED", "false"),
    ):
        monkeypatch.delenv(name, raising=False)
        with pytest.raises(ValueError, match="demo_paper_environment_required"):
            round_trip._check_environment()
        monkeypatch.setenv(name, value)

    monkeypatch.setattr(
        round_trip,
        "EnvironmentSettings",
        lambda: SimpleNamespace(
            broker_env="demo", trading_mode="paper", live_trading_enabled=False
        ),
    )
    assert round_trip._check_environment().broker_env == "demo"


@pytest.mark.parametrize(
    ("name", "bad_value"),
    [
        ("BROKER_ENV", "live"),
        ("TRADING_MODE", "auto"),
        ("LIVE_TRADING_ENABLED", "true"),
    ],
)
def test_environment_guard_rejects_unsafe_values(
    monkeypatch: pytest.MonkeyPatch, name: str, bad_value: str
) -> None:
    for key, value in (
        ("BROKER_ENV", "demo"),
        ("TRADING_MODE", "paper"),
        ("LIVE_TRADING_ENABLED", "false"),
    ):
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(name, bad_value)
    with pytest.raises(ValueError, match="demo_paper_environment_required"):
        round_trip._check_environment()


def test_stop_is_rounded_down_to_tick() -> None:
    assert round_trip._floor_to_tick(Decimal("100.19"), Decimal("0.1")) == Decimal("100.1")


def test_market_preflight_uses_broker_minimum_size_stop_distance_and_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {
        "version": 1,
        "instruments": {
            "NAS100": {
                "display_name": "Nasdaq-100",
                "aliases": ["NAS100"],
                "capital_epic": "US100",
                "histdata_code": "NSXUSD",
                "asset_class": "index",
                "value_per_point": None,
            }
        },
    }
    monkeypatch.setattr(
        round_trip,
        "load_config_file",
        lambda path: SimpleNamespace(model_dump=lambda: config),
    )

    ask, size, step, tick, epic, minimum_distance = round_trip._market_inputs(
        round_trip.ObservedTradingClient(FakeClient()), round_trip.Path("config/instruments.yaml")
    )

    assert (ask, size, step, tick, epic, minimum_distance) == (
        Decimal("20000.0"),
        Decimal("0.1"),
        Decimal("0.1"),
        Decimal("0.1"),
        "US100",
        Decimal("2"),
    )


def test_observer_records_safe_position_values_and_rounding_without_credentials() -> None:
    client = FakeClient()
    client.position_rows = [
        {
            "position": {
                "dealId": "deal-1",
                "direction": "BUY",
                "size": 0.2,
                "level": 20000.1,
                "stopLevel": 19997.8,
            },
            "market": {"epic": "US100"},
        }
    ]
    observed = round_trip.ObservedTradingClient(client)  # type: ignore[arg-type]
    observed.get_positions()
    report: dict[str, object] = {
        "epic": "US100",
        "requested_size": "0.1",
        "stop_levels": {"open_requested": "19997.7", "tightened_requested": "19997.8"},
        "rounding": {},
    }

    round_trip._record_rounding(report, observed)

    assert report["broker_observed_size"] == 0.2
    assert report["broker_observed_stop"] == 19997.8
    assert report["rounding"] == {
        "size_delta": "0.1",
        "open_stop_delta": "0.1",
        "tightened_stop_delta": "0.0",
    }
    assert "client" not in report
    assert "password" not in str(report).lower()
