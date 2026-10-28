from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from trader.data.instruments import (
    InstrumentDataError,
    InstrumentMapper,
    UnknownInstrumentError,
)


def _config() -> dict[str, Any]:
    return {
        "version": 1,
        "instruments": {
            "NAS100": {
                "display_name": "Nasdaq-100",
                "aliases": ["NAS100", "US100", "NSXUSD", "NASDAQ100"],
                "capital_epic": "US100",
                "histdata_code": "NSXUSD",
                "asset_class": "index",
                "value_per_point": None,
            },
            "XAUUSD": {
                "display_name": "Gold",
                "aliases": ["XAUUSD", "GOLD", "XAU"],
                "capital_epic": "GOLD",
                "histdata_code": "XAUUSD",
                "asset_class": "commodity",
                "value_per_point": None,
            },
        },
    }


def _details(epic: str = "US100") -> dict[str, Any]:
    return {
        "instrument": {
            "epic": epic,
            "lotSize": 1,
            "marginFactor": 5,
            "marginFactorUnit": "PERCENTAGE",
            "currency": "USD",
        },
        "dealingRules": {
            "minDealSize": {"value": 0.1, "unit": "POINTS"},
            "maxDealSize": {"value": 100, "unit": "POINTS"},
            "minSizeIncrement": {"value": 0.1, "unit": "POINTS"},
        },
        "snapshot": {"marketStatus": "CLOSED"},
    }


class _Client:
    def __init__(self) -> None:
        self.search_calls: list[str] = []
        self.details_calls: list[str] = []
        self.details: dict[str, Any] = _details()
        self.search_result: list[dict[str, Any]] = [
            {"epic": "US100", "tickSize": 0.1},
        ]

    def search_markets(self, search_term: str) -> list[dict[str, Any]]:
        self.search_calls.append(search_term)
        return self.search_result

    def market_details(self, epic: str) -> dict[str, Any]:
        self.details_calls.append(epic)
        return self.details


def test_config_aliases_resolve_without_symbol_specific_code() -> None:
    mapper = InstrumentMapper(_config(), _Client())

    assert mapper.resolve("nasdaq100").symbol == "NAS100"
    assert mapper.resolve("NSXUSD").capital_epic == "US100"
    assert mapper.resolve("xAu").symbol == "XAUUSD"
    with pytest.raises(UnknownInstrumentError, match="unknown instrument alias: DAX"):
        mapper.resolve("dax")


def test_market_details_are_validated_and_exposed_as_decimal_terms() -> None:
    mapper = InstrumentMapper(_config(), _Client(), clock=lambda: datetime(2026, 10, 3, tzinfo=UTC))

    terms = mapper.get("US100")

    assert terms.symbol == "NAS100"
    assert terms.capital_epic == "US100"
    assert terms.tick_size == Decimal("0.1")
    assert terms.contract_size == Decimal("1")
    assert terms.min_size == Decimal("0.1")
    assert terms.max_size == Decimal("100")
    assert terms.size_step == Decimal("0.1")
    assert terms.size_unit == "POINTS"
    assert terms.margin_factor == Decimal("5")
    assert terms.margin_factor_unit == "PERCENTAGE"
    assert terms.value_per_point == Decimal("1")  # lotSize fallback, no config value
    assert terms.market_status == "CLOSED"
    assert terms.updated_at == datetime(2026, 10, 3, tzinfo=UTC)


def test_market_details_cache_is_shared_by_alias_and_refreshes_after_ttl() -> None:
    now = [100.0]
    client = _Client()
    mapper = InstrumentMapper(
        _config(),
        client,
        refresh_interval_seconds=60,
        monotonic=lambda: now[0],
    )

    first = mapper.get("NAS100")
    assert mapper.get("US100") is first
    assert client.search_calls == ["US100"]
    assert client.details_calls == ["US100"]

    now[0] += 60
    mapper.get("NSXUSD")
    assert client.search_calls == ["US100", "US100"]
    assert client.details_calls == ["US100", "US100"]


def test_mapper_requires_an_exact_broker_search_match() -> None:
    client = _Client()
    client.search_result = [{"epic": "US500", "tickSize": 0.1}]

    with pytest.raises(InstrumentDataError, match="returned 0 exact matches"):
        InstrumentMapper(_config(), client).get("NAS100")


def test_mapper_rejects_inconsistent_broker_size_rules() -> None:
    client = _Client()
    client.details["dealingRules"]["minSizeIncrement"] = {"value": 0.1, "unit": "OTHER"}

    with pytest.raises(InstrumentDataError, match="size rule units do not match"):
        InstrumentMapper(_config(), client).get("NAS100")


def test_mapper_rejects_a_mismatched_market_details_epic() -> None:
    client = _Client()
    client.details = _details("US500")

    with pytest.raises(InstrumentDataError, match="does not match US100"):
        InstrumentMapper(_config(), client).get("NAS100")


def test_adding_instrument_is_a_config_only_change() -> None:
    config = _config()
    config["instruments"]["DE40"] = {
        "display_name": "Germany 40",
        "aliases": ["DE40", "DAX"],
        "capital_epic": "GER40",
        "histdata_code": "DE40",
        "asset_class": "index",
        "value_per_point": None,
    }

    mapper = InstrumentMapper(config, _Client())

    assert mapper.resolve("DAX").capital_epic == "GER40"


def test_point_value_prefers_config_then_broker_field_then_lot_size() -> None:
    config = _config()
    client = _Client()
    assert InstrumentMapper(config, client).get("US100").value_per_point == Decimal("1")

    client.details["instrument"]["valuePerPoint"] = 2
    assert InstrumentMapper(config, client).get("US100").value_per_point == Decimal("2")

    config["instruments"]["NAS100"]["value_per_point"] = 3
    assert InstrumentMapper(config, client).get("US100").value_per_point == Decimal("3")


def test_guardian_sizing_works_from_live_shaped_us100_terms() -> None:
    """The sizer must not refuse US100 for a missing point value."""
    from trader.agents.graph import TradeIntent
    from trader.risk.guardian import AccountState
    from trader.risk.sizing import SizingLimits
    from trader.runtime.context import SizingContextSource

    mapper = InstrumentMapper(_config(), _Client())
    source = SizingContextSource(
        mapper.get,
        SizingLimits(Decimal("0.5"), Decimal("40"), Decimal("5")),
        account_currency=lambda _account: "USD",
    )
    intent = TradeIntent(
        instrument="NAS100",
        direction="BUY",
        entry_type="market",
        entry_level=Decimal("100"),
        stop_level=Decimal("90"),
        target_levels=(Decimal("120"),),
        time_horizon="intraday",
        thesis="fixture",
        confidence=1.0,
    )
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
    estimate = source(intent, account, ())
    assert estimate.size > 0
    assert estimate.risk_amount <= Decimal("5")  # 0.5% of 1000
