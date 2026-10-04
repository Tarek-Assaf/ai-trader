from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import pytest

from trader.data.instruments import InstrumentTerms
from trader.risk.guardian import AccountState, TradeIntent
from trader.risk.sizing import SizingLimits, size_position


def _terms(
    symbol: str = "NAS100",
    *,
    value_per_point: Decimal | None = Decimal("1"),
    currency: str = "USD",
    contract_size: Decimal = Decimal("1"),
    min_size: Decimal = Decimal("0.1"),
    max_size: Decimal = Decimal("100"),
    size_step: Decimal = Decimal("0.1"),
    margin_factor: Decimal = Decimal("5"),
    margin_factor_unit: str = "PERCENTAGE",
) -> InstrumentTerms:
    return InstrumentTerms(
        symbol=symbol,
        capital_epic="US100" if symbol == "NAS100" else "GOLD",
        tick_size=Decimal("0.1"),
        contract_size=contract_size,
        min_size=min_size,
        max_size=max_size,
        size_step=size_step,
        size_unit="POINTS",
        margin_factor=margin_factor,
        margin_factor_unit=margin_factor_unit,
        value_per_point=value_per_point,
        currency=currency,
        market_status="TRADEABLE",
        updated_at=datetime(2026, 10, 4, tzinfo=UTC),
    )


def _account(
    *,
    equity: Decimal = Decimal("10000"),
    margin_used: Decimal = Decimal("0"),
    notional_exposure: Decimal = Decimal("0"),
) -> AccountState:
    return AccountState(
        equity=equity,
        day_start_equity=equity,
        week_start_equity=equity,
        peak_equity=equity,
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        margin_used=margin_used,
        notional_exposure=notional_exposure,
        orders_last_hour=0,
        consecutive_losses=0,
        last_loss_at=None,
    )


def _intent(
    *,
    symbol: str = "NAS100",
    direction: Literal["buy", "sell"] = "buy",
    entry: Decimal = Decimal("100"),
    stop: Decimal | None = Decimal("97"),
) -> TradeIntent:
    return TradeIntent(
        instrument=symbol,
        direction=direction,
        entry_price=entry,
        stop_loss=stop,
        take_profit=None,
    )


def _limits(
    *,
    risk_pct: Decimal = Decimal("0.5"),
    max_margin_pct: Decimal = Decimal("40"),
    max_leverage: Decimal = Decimal("5"),
) -> SizingLimits:
    return SizingLimits(
        risk_per_trade_pct=risk_pct,
        max_margin_usage_pct=max_margin_pct,
        max_effective_leverage=max_leverage,
    )


def test_position_size_rounds_down_and_stays_within_risk_budget() -> None:
    decision = size_position(
        _intent(),
        _terms(),
        account_equity=Decimal("10000"),
        account_currency="USD",
        current_margin_used=Decimal("0"),
        current_notional_exposure=Decimal("0"),
        limits=_limits(),
    )

    assert decision.approved
    assert decision.size == Decimal("16.6")
    assert decision.risk_budget == Decimal("50")
    assert decision.estimated_risk == Decimal("49.8")
    assert decision.estimated_risk <= decision.risk_budget


@pytest.mark.parametrize(
    ("symbol", "value_per_point", "contract_size"),
    [
        ("NAS100", Decimal("1"), Decimal("1")),
        ("XAUUSD", Decimal("0.1"), Decimal("100")),
    ],
)
def test_sizing_supports_each_configured_instrument(
    symbol: str, value_per_point: Decimal, contract_size: Decimal
) -> None:
    decision = size_position(
        _intent(symbol=symbol),
        _terms(
            symbol,
            value_per_point=value_per_point,
            contract_size=contract_size,
            max_size=Decimal("0.1") if symbol == "XAUUSD" else Decimal("100"),
        ),
        account_equity=Decimal("10000"),
        account_currency="USD",
        current_margin_used=Decimal("0"),
        current_notional_exposure=Decimal("0"),
        limits=_limits(max_leverage=Decimal("100")),
    )

    assert decision.approved
    assert decision.size is not None
    assert decision.estimated_risk is not None
    assert decision.estimated_risk <= decision.risk_budget


def test_rejects_when_minimum_broker_size_exceeds_risk_budget() -> None:
    decision = size_position(
        _intent(entry=Decimal("200"), stop=Decimal("100")),
        _terms(min_size=Decimal("1"), size_step=Decimal("1")),
        account_equity=Decimal("10000"),
        account_currency="USD",
        current_margin_used=Decimal("0"),
        current_notional_exposure=Decimal("0"),
        limits=_limits(),
    )

    assert not decision.approved
    assert decision.size is None
    assert decision.reason == "minimum broker size exceeds the risk budget"


def test_rejects_projected_margin_over_limit() -> None:
    decision = size_position(
        _intent(entry=Decimal("1000"), stop=Decimal("999")),
        _terms(margin_factor=Decimal("5")),
        account_equity=Decimal("10000"),
        account_currency="USD",
        current_margin_used=Decimal("0"),
        current_notional_exposure=Decimal("0"),
        limits=_limits(max_margin_pct=Decimal("10"), max_leverage=Decimal("100")),
    )

    assert not decision.approved
    assert decision.size is None
    assert decision.reason == "projected margin usage exceeds the configured limit"


def test_rejects_projected_effective_leverage_over_limit() -> None:
    decision = size_position(
        _intent(entry=Decimal("1000"), stop=Decimal("999")),
        _terms(margin_factor=Decimal("1")),
        account_equity=Decimal("10000"),
        account_currency="USD",
        current_margin_used=Decimal("0"),
        current_notional_exposure=Decimal("0"),
        limits=_limits(max_margin_pct=Decimal("90"), max_leverage=Decimal("4")),
    )

    assert not decision.approved
    assert decision.size is None
    assert decision.reason == "projected effective leverage exceeds the configured limit"


def test_currency_conversion_is_applied_to_risk_and_margin() -> None:
    decision = size_position(
        _intent(entry=Decimal("100"), stop=Decimal("98")),
        _terms(currency="EUR", margin_factor=Decimal("5")),
        account_equity=Decimal("10000"),
        account_currency="USD",
        current_margin_used=Decimal("0"),
        current_notional_exposure=Decimal("0"),
        limits=_limits(max_leverage=Decimal("100")),
        quote_to_account_rate=Decimal("1.2"),
    )

    assert decision.approved
    assert decision.size == Decimal("20.8")
    assert decision.risk_budget == Decimal("50")
    assert decision.estimated_risk == Decimal("49.92")
    assert decision.margin_required == Decimal("124.800")


def test_currency_mismatch_without_conversion_rate_fails_closed() -> None:
    decision = size_position(
        _intent(),
        _terms(currency="EUR"),
        account_equity=Decimal("10000"),
        account_currency="USD",
        current_margin_used=Decimal("0"),
        current_notional_exposure=Decimal("0"),
        limits=_limits(),
    )

    assert not decision.approved
    assert decision.size is None
    assert decision.reason == "a positive quote-to-account conversion rate is required"


def test_missing_broker_value_per_point_fails_closed() -> None:
    decision = size_position(
        _intent(),
        _terms(value_per_point=None),
        account_equity=Decimal("10000"),
        account_currency="USD",
        current_margin_used=Decimal("0"),
        current_notional_exposure=Decimal("0"),
        limits=_limits(),
    )

    assert not decision.approved
    assert decision.size is None
    assert decision.reason == "broker value_per_point is unavailable or invalid"


@pytest.mark.parametrize(
    ("direction", "entry", "stop"),
    [
        ("buy", Decimal("100"), Decimal("100")),
        ("buy", Decimal("100"), Decimal("101")),
        ("sell", Decimal("100"), Decimal("99")),
    ],
)
def test_rejects_missing_or_wrong_side_stop(
    direction: Literal["buy", "sell"], entry: Decimal, stop: Decimal
) -> None:
    decision = size_position(
        _intent(direction=direction, entry=entry, stop=stop),
        _terms(),
        account_equity=Decimal("10000"),
        account_currency="USD",
        current_margin_used=Decimal("0"),
        current_notional_exposure=Decimal("0"),
        limits=_limits(),
    )

    assert not decision.approved
    assert decision.size is None
    assert decision.reason is not None
