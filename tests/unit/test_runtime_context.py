"""Production Guardian context sources fail closed on missing or stale inputs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from trader.agents.graph import TradeIntent
from trader.data.instruments import InstrumentTerms
from trader.execution.brokers.base import Direction, Position
from trader.risk.guardian import OpenPosition
from trader.risk.sizing import SizingLimits
from trader.runtime.context import (
    AccountContextSource,
    ContextUnavailable,
    MarketContextSource,
    RuntimeContextProvider,
    SizingContextSource,
    position_source,
)

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


class Journal:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict[str, object]] = {}

    def get_snapshot(self, snapshot_id: str) -> dict[str, object] | None:
        return self.snapshots.get(snapshot_id)

    def save_snapshot(self, snapshot_id: str, payload: dict[str, object]) -> None:
        self.snapshots[snapshot_id] = payload

    def records(self) -> tuple[dict[str, object], ...]:
        return ()


class Broker:
    def get_account(self) -> dict[str, object]:
        return {
            "equity": "10000",
            "day_start_equity": "10000",
            "week_start_equity": "10000",
            "peak_equity": "10000",
            "daily_pnl": "0",
            "weekly_pnl": "0",
            "margin_used": "0",
            "notional_exposure": "0",
            "orders_last_hour": 0,
            "consecutive_losses": 0,
            "last_loss_at": None,
        }

    def get_positions(self) -> tuple[Position, ...]:
        return (
            Position(
                "deal-1",
                "NAS100",
                Direction.BUY,
                Decimal("1"),
                Decimal("100"),
                Decimal("99"),
                Decimal("102"),
            ),
        )


class Recorder:
    def __init__(self, stamp: datetime = NOW) -> None:
        self.stamp = stamp

    def latest_snapshot(self, _instrument: str) -> dict[str, object]:
        return {
            "latest_data_at": self.stamp,
            "bid": Decimal("99.9"),
            "ask": Decimal("100.1"),
            "atr": Decimal("1"),
            "market_open": True,
            "in_break": False,
            "spread_ceiling": Decimal("0.5"),
        }


def _intent() -> TradeIntent:
    return TradeIntent(
        instrument="NAS100",
        direction="BUY",
        entry_type="market",
        entry_level=Decimal("100"),
        stop_level=Decimal("99"),
        target_levels=(Decimal("102"),),
        time_horizon="intraday",
        thesis="context fixture",
        confidence=0.9,
    )


def _terms() -> InstrumentTerms:
    return InstrumentTerms(
        symbol="NAS100",
        capital_epic="US100",
        tick_size=Decimal("0.1"),
        contract_size=Decimal("1"),
        min_size=Decimal("0.1"),
        max_size=Decimal("10"),
        size_step=Decimal("0.1"),
        size_unit="POINTS",
        margin_factor=Decimal("5"),
        margin_factor_unit="PERCENTAGE",
        value_per_point=Decimal("1"),
        currency="USD",
        market_status="TRADEABLE",
        updated_at=NOW,
    )


def test_context_sources_build_account_market_size_and_positions() -> None:
    broker, journal = Broker(), Journal()
    account = AccountContextSource(broker, journal, clock=lambda: NOW)()
    market = MarketContextSource(Recorder(), clock=lambda: NOW)("NAS100")
    sizing = SizingContextSource(
        lambda _instrument: _terms(),
        SizingLimits(Decimal("0.5"), Decimal("40"), Decimal("5")),
        account_currency=lambda _account: "USD",
    )(_intent(), account, ())
    positions = position_source(
        broker,
        valuation=lambda _position: OpenPosition(
            "NAS100", Decimal("10"), Decimal("5"), Decimal("100")
        ),
    )()
    provider = RuntimeContextProvider(
        lambda: account,
        lambda _instrument: market,
        lambda _intent, _account, _positions: sizing,
        lambda: positions,
        clock=lambda: NOW,
    )
    context = provider("cycle-1", _intent())
    assert context is not None
    assert context.market.ask == Decimal("100.1")
    assert context.sizing is not None and context.sizing.size > 0
    assert context.open_positions[0].margin_required == Decimal("5")
    assert journal.snapshots["runtime:pnl-baseline"]["peak_equity"] == "10000"


def test_account_baseline_seeds_reloads_and_rolls_the_utc_day() -> None:
    broker, journal = Broker(), Journal()
    current = [NOW]
    source = AccountContextSource(broker, journal, clock=lambda: current[0])
    first = source()
    assert first.day_start_equity == Decimal("10000")
    assert first.week_start_equity == Decimal("10000")
    assert first.peak_equity == Decimal("10000")

    class HigherEquityBroker(Broker):
        def get_account(self) -> dict[str, object]:
            account = super().get_account()
            account["equity"] = "10100"
            return account

    restarted = AccountContextSource(HigherEquityBroker(), journal, clock=lambda: current[0])()
    assert restarted.day_start_equity == Decimal("10000")
    assert restarted.week_start_equity == Decimal("10000")
    assert restarted.peak_equity == Decimal("10100")

    current[0] = NOW + timedelta(days=1)
    rolled = AccountContextSource(HigherEquityBroker(), journal, clock=lambda: current[0])()
    assert rolled.day_start_equity == Decimal("10100")
    assert rolled.week_start_equity == Decimal("10000")
    assert rolled.peak_equity == Decimal("10100")


def test_missing_account_fields_and_stale_market_reject_without_defaults() -> None:
    class IncompleteBroker:
        def get_account(self) -> dict[str, object]:
            return {"equity": "10000"}

    try:
        AccountContextSource(IncompleteBroker(), Journal(), clock=lambda: NOW)()
    except ContextUnavailable as exc:
        assert "margin_used" in str(exc)
    else:
        raise AssertionError("missing account baseline must reject")

    stale = MarketContextSource(
        Recorder(NOW - timedelta(minutes=4)), clock=lambda: NOW, stale_after_seconds=180
    )
    try:
        stale("NAS100")
    except ContextUnavailable as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("stale market data must reject")


def test_paper_broker_account_builds_guardian_context_with_a_declared_loss_streak() -> None:
    """The production paper broker reports no loss streak; the deployment declares one."""
    from trader.execution.brokers.paper import PaperBroker, PaperExecutionCosts

    broker = PaperBroker(
        starting_cash=Decimal("1000"),
        currency="USD",
        costs=PaperExecutionCosts(
            spread_points={"US100": Decimal("1")}, slippage_points={"US100": Decimal("0")}
        ),
    )
    strict = AccountContextSource(broker, Journal(), clock=lambda: NOW)
    try:
        strict()
    except ContextUnavailable as exc:
        assert "consecutive_losses" in str(exc)
    else:
        raise AssertionError("an unknown loss streak must reject by default")

    state = AccountContextSource(
        broker, Journal(), clock=lambda: NOW, default_consecutive_losses=0
    )()
    assert state.equity == Decimal("1000")
    assert state.consecutive_losses == 0
    assert state.margin_used == Decimal("0")


def test_context_provider_logs_why_it_returned_nothing(caplog) -> None:
    import logging

    def broken_account():
        raise ContextUnavailable("consecutive_losses is unavailable")

    provider = RuntimeContextProvider(broken_account, lambda _i: None, lambda *_a: None, lambda: ())
    with caplog.at_level(logging.WARNING):
        assert provider("cycle-1", _intent()) is None
    assert "consecutive_losses is unavailable" in caplog.text


def test_provider_passes_the_stage_cap_derived_from_the_sizing() -> None:
    from types import SimpleNamespace

    sizing = SimpleNamespace(size=Decimal("0.002"))
    provider = RuntimeContextProvider(
        lambda: "account",
        lambda _i: "market",
        lambda *_a: sizing,
        lambda: (),
        stage_cap=lambda estimate: estimate.size,
        clock=lambda: NOW,
    )
    context = provider("cycle-1", _intent())
    assert context is not None and context.stage_size_cap == Decimal("0.002")
    no_cap = RuntimeContextProvider(
        lambda: "account", lambda _i: "market", lambda *_a: sizing, lambda: (), clock=lambda: NOW
    )("cycle-1", _intent())
    assert no_cap is not None and no_cap.stage_size_cap is None


def test_paper_stage_cap_exists_only_for_paper_mode_on_the_demo_environment() -> None:
    from types import SimpleNamespace

    from trader.runtime.services import _paper_stage_cap

    sizing = SimpleNamespace(size=Decimal("0.5"))
    cap = _paper_stage_cap(SimpleNamespace(trading_mode="paper", broker_env="demo"))
    assert cap is not None and cap(sizing) == Decimal("0.5")
    assert _paper_stage_cap(SimpleNamespace(trading_mode="auto", broker_env="demo")) is None
    assert _paper_stage_cap(SimpleNamespace(trading_mode="paper", broker_env="live")) is None


def test_tight_stop_is_scaled_down_to_the_margin_and_leverage_limits() -> None:
    from dataclasses import replace as dc_replace

    terms = dc_replace(
        _terms(),
        min_size=Decimal("0.001"),
        size_step=Decimal("0.001"),
        max_size=Decimal("100"),
    )
    account = AccountContextSource(Broker(), Journal(), clock=lambda: NOW)()
    intent = TradeIntent(
        instrument="NAS100",
        direction="BUY",
        entry_type="market",
        entry_level=Decimal("29800"),
        stop_level=Decimal("29788"),  # 12 points: risk sizing alone asks for ~4 units
        target_levels=(Decimal("29836"),),
        time_horizon="intraday",
        thesis="t",
        confidence=1.0,
    )
    source = SizingContextSource(
        lambda _i: terms,
        SizingLimits(Decimal("0.5"), Decimal("40"), Decimal("5")),
        account_currency=lambda _a: "USD",
    )
    estimate = source(intent, account, ())
    assert Decimal("0") < estimate.size < Decimal("4")
    assert estimate.size * Decimal("29800") <= Decimal("5") * account.equity
    assert estimate.risk_amount <= account.equity * Decimal("0.005")


def test_sizing_still_rejects_when_even_the_minimum_size_breaks_the_limits() -> None:
    from dataclasses import replace as dc_replace

    terms = dc_replace(
        _terms(), min_size=Decimal("5"), size_step=Decimal("5"), max_size=Decimal("50")
    )
    account = AccountContextSource(Broker(), Journal(), clock=lambda: NOW)()
    intent = TradeIntent(
        instrument="NAS100",
        direction="BUY",
        entry_type="market",
        entry_level=Decimal("29800"),
        stop_level=Decimal("29700"),
        target_levels=(Decimal("30100"),),
        time_horizon="intraday",
        thesis="t",
        confidence=1.0,
    )
    source = SizingContextSource(
        lambda _i: terms,
        SizingLimits(Decimal("0.5"), Decimal("40"), Decimal("5")),
        account_currency=lambda _a: "USD",
    )
    with pytest.raises(ContextUnavailable):
        source(intent, account, ())


def test_broker_pnl_provider_reports_period_realized_and_floating(tmp_path: Any) -> None:
    from decimal import Decimal

    from trader.runtime.services import _broker_pnl_provider

    balances = ["11000.00", "11012.50"]

    class Broker:
        def get_account(self) -> dict[str, object]:
            return {
                "balance": balances[0],
                "equity": str(Decimal(balances[0]) - Decimal("2.50")),
                "currency": "USD",
                "margin_used": "300",
                "notional_exposure": "3000",
            }

    provider = _broker_pnl_provider(Broker(), tmp_path / "state" / "pnl_baselines.json")
    first = provider("daily")
    assert first.realized_net == Decimal("0") and first.unrealized_net == Decimal("-2.50")
    balances.pop(0)  # a trade closed: balance moved +12.50 within the same day
    report = provider("daily")
    assert report.realized_net == Decimal("12.50")
    assert report.margin_used == Decimal("300") and report.total_exposure == Decimal("3000")
    assert provider("monthly").realized_net == Decimal("12.50")
