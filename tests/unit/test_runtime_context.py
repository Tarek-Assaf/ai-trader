"""Production Guardian context sources fail closed on missing or stale inputs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
        self.snapshot: dict[str, object] | None = None

    def get_snapshot(self, _snapshot_id: str) -> dict[str, object] | None:
        return self.snapshot

    def save_snapshot(self, _snapshot_id: str, payload: dict[str, object]) -> None:
        self.snapshot = payload

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
    assert journal.snapshot is not None


def test_missing_baseline_and_stale_market_reject_without_defaults() -> None:
    class IncompleteBroker:
        def get_account(self) -> dict[str, object]:
            return {"equity": "10000"}

    try:
        AccountContextSource(IncompleteBroker(), Journal(), clock=lambda: NOW)()
    except ContextUnavailable as exc:
        assert "day_start_equity" in str(exc)
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
