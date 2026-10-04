from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "demo_round_trip.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location("demo_round_trip", _SCRIPT_PATH)
if _SCRIPT_SPEC is None or _SCRIPT_SPEC.loader is None:
    raise ImportError("could not load demo round-trip script")
round_trip = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(round_trip)


class FakeClient:
    environment = "demo"

    def __init__(self) -> None:
        self.position_rows: list[dict[str, Any]] = []
        self.references: list[tuple[str, str]] = []
        self.market_payload: dict[str, object] | None = None

    def search_markets(self, term: str) -> list[dict[str, object]]:
        assert term == "US100"
        return [{"epic": "US100", "tickSize": 0.1}]

    def market_details(self, epic: str) -> dict[str, object]:
        assert epic == "US100"
        if self.market_payload is not None:
            return self.market_payload
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
                "minStepDistance": {"value": 999, "unit": "POINTS"},
                "minNormalStopOrLimitDistance": {"value": 2, "unit": "POINTS"},
            },
            "snapshot": {"marketStatus": "TRADEABLE", "bid": 19999.8, "offer": 20000.0},
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

    bid, ask, size, step, tick, epic, minimum_distance = round_trip._market_inputs(
        round_trip.ObservedTradingClient(FakeClient()), round_trip.Path("config/instruments.yaml")
    )

    assert (bid, ask, size, step, tick, epic, minimum_distance) == (
        Decimal("19999.8"),
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


def test_stop_plan_uses_bid_and_spread_margin_and_preserves_minimum_after_tighten() -> None:
    spread, margin, stop, tightened = round_trip._stop_plan(
        Decimal("100.0"),
        Decimal("100.4"),
        Decimal("0.1"),
        Decimal("2"),
    )

    assert (spread, margin, stop, tightened) == (
        Decimal("0.4"),
        Decimal("0.5"),
        Decimal("97.5"),
        Decimal("97.6"),
    )
    assert Decimal("100.0") - tightened >= Decimal("2")


def test_market_inputs_falls_back_to_min_stop_or_profit_distance(
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
    client = FakeClient()
    details = client.market_details("US100")
    rules = details["dealingRules"]
    assert isinstance(rules, dict)
    rules.pop("minNormalStopOrLimitDistance")
    rules["minStopOrProfitDistance"] = {"value": 3, "unit": "POINTS"}
    client.market_payload = details

    result = round_trip._market_inputs(
        round_trip.ObservedTradingClient(client),
        round_trip.Path("config/instruments.yaml"),
    )

    assert result[-1] == Decimal("3")


def test_market_inputs_rejects_non_points_stop_distance(
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
    client = FakeClient()
    details = client.market_details("US100")
    rules = details["dealingRules"]
    assert isinstance(rules, dict)
    rules["minNormalStopOrLimitDistance"] = {"value": 2, "unit": "PERCENTAGE"}
    client.market_payload = details

    with pytest.raises(ValueError, match="minimum_stop_distance_unit_unsupported"):
        round_trip._market_inputs(
            round_trip.ObservedTradingClient(client),
            round_trip.Path("config/instruments.yaml"),
        )


def test_preflight_is_read_only_and_does_not_create_one_shot_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class ReadOnlyClient:
        environment = "demo"

        def get_positions(self) -> list[dict[str, Any]]:
            return []

        def get_working_orders(self) -> list[dict[str, Any]]:
            return []

        def close(self) -> None:
            return None

    for name, value in (
        ("BROKER_ENV", "demo"),
        ("TRADING_MODE", "paper"),
        ("LIVE_TRADING_ENABLED", "false"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        round_trip,
        "EnvironmentSettings",
        lambda: SimpleNamespace(
            broker_env="demo",
            trading_mode="paper",
            live_trading_enabled=False,
        ),
    )
    monkeypatch.setattr(round_trip, "WORKSPACE", tmp_path)
    monkeypatch.setattr(round_trip, "LOCK_PATH", tmp_path / ".once")
    monkeypatch.setattr(
        round_trip,
        "CapitalComTradingClient",
        lambda settings: ReadOnlyClient(),
    )
    monkeypatch.setattr(
        round_trip,
        "_market_inputs",
        lambda client, path: (
            Decimal("19999.8"),
            Decimal("20000.0"),
            Decimal("0.1"),
            Decimal("0.1"),
            Decimal("0.1"),
            "US100",
            Decimal("2"),
        ),
    )

    result = round_trip.run_round_trip(["--preflight"])
    output = capsys.readouterr().out

    assert result == 0
    assert '"result": "preflight"' in output
    assert '"tightened_requested": "19997.4"' in output
    assert not round_trip.LOCK_PATH.exists()
