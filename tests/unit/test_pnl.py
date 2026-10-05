"""Unit tests for deterministic account-currency P&L calculations."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trader.execution.brokers.base import Direction, OrderRequest
from trader.execution.brokers.paper import PaperBroker, PaperExecutionCosts
from trader.execution.pnl import (
    BrokerAccountFigures,
    EquityPoint,
    PnlEngine,
    PnlTrade,
)

D = Decimal


def _broker(
    *, balance: str, equity: str, margin: str = "0", exposure: str = "0"
) -> BrokerAccountFigures:
    return BrokerAccountFigures(
        balance=D(balance),
        equity=D(equity),
        currency="USD",
        margin_used=D(margin),
        notional_exposure=D(exposure),
    )


def test_closed_trade_reconciles_to_paper_broker_account() -> None:
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    paper = PaperBroker(
        starting_cash=D("1000"),
        currency="USD",
        costs=PaperExecutionCosts(
            spread_points={"NAS100": D("2")},
            slippage_points={"NAS100": D("0.5")},
        ),
        clock=lambda: now,
    )
    receipt = paper.place_order(
        OrderRequest(
            client_order_id="pnl-round-trip-1",
            epic="NAS100",
            direction=Direction.BUY,
            size=D("2"),
            reference_price=D("100"),
            stop_level=D("95"),
        )
    )
    # Without a live quote, PaperBroker applies half-spread plus slippage.
    assert receipt.position.level == D("101.5")

    paper.update_quote("NAS100", bid=D("104"), ask=D("106"))
    paper.close_position(receipt.position.deal_id)
    activity = paper.get_activity(now, now)
    close_event = next(event for event in activity if event["action"] == "closed")
    assert close_event["level"] == D("103.5")

    account = paper.get_account()
    report = PnlEngine.calculate(
        [
            PnlTrade(
                trade_id=receipt.position.deal_id,
                epic="NAS100",
                direction=Direction.BUY,
                size=receipt.position.size,
                entry_price=receipt.position.level,
                stop_level=D("95"),
                value_per_point=D("1"),
                notional_exposure=D("0"),
                margin_used=D("0"),
                exit_price=close_event["level"],
            )
        ],
        starting_balance=D("1000"),
        currency="usd",
        broker=BrokerAccountFigures(
            balance=account["balance"],  # type: ignore[arg-type]
            equity=account["equity"],  # type: ignore[arg-type]
            currency=str(account["currency"]),
            margin_used=D("0"),
            notional_exposure=D("0"),
        ),
        as_of=now,
    )

    assert report.realized_gross == D("4.0")
    assert report.realized_net == D("4.0")
    assert report.balance == D("1004.0")
    assert report.equity == D("1004.0")
    assert report.r_multiples[0].initial_risk == D("13.0")
    assert report.r_multiples[0].r_multiple == D("4.0") / D("13.0")
    assert report.reconciliation.alert_required is False
    assert report.equity_curve[-1] == EquityPoint(now, D("1004.0"))


def test_open_and_closed_trades_calculate_equity_drawdown_and_exposure() -> None:
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    report = PnlEngine.calculate(
        [
            PnlTrade(
                trade_id="closed-1",
                epic="NAS100",
                direction=Direction.BUY,
                size=D("2"),
                entry_price=D("100"),
                stop_level=D("95"),
                value_per_point=D("1"),
                notional_exposure=D("0"),
                margin_used=D("0"),
                exit_price=D("104"),
                fees=D("1"),
                financing=D("-0.2"),
            ),
            PnlTrade(
                trade_id="open-1",
                epic="XAUUSD",
                direction=Direction.SELL,
                size=D("1"),
                entry_price=D("50"),
                stop_level=D("55"),
                value_per_point=D("2"),
                notional_exposure=D("500"),
                margin_used=D("100"),
                mark_price=D("47"),
                fees=D("0.5"),
                financing=D("-0.1"),
            ),
        ],
        starting_balance=D("1000"),
        currency="USD",
        broker=_broker(balance="1006.8", equity="1012.2", margin="100", exposure="500"),
        as_of=now,
        equity_history=[EquityPoint(now - timedelta(hours=1), D("1050"))],
    )

    assert report.realized_gross == D("8")
    assert report.realized_net == D("6.8")
    assert report.unrealized_gross == D("6")
    assert report.unrealized_net == D("5.4")
    assert report.fees == D("1.5")
    assert report.financing == D("-0.3")
    assert report.balance == D("1006.8")
    assert report.equity == D("1012.2")
    assert report.peak_equity == D("1050")
    assert report.drawdown == D("37.8")
    assert report.drawdown_pct == D("37.8") / D("1050") * D("100")
    assert dict(report.exposure_by_epic) == {"XAUUSD": D("500")}
    assert report.total_exposure == D("500")
    assert report.margin_used == D("100")
    assert report.margin_usage_pct == D("100") / D("1012.2") * D("100")
    assert report.effective_leverage == D("500") / D("1012.2")
    assert report.r_multiples[0].net_profit == D("6.8")
    assert report.r_multiples[0].initial_risk == D("10")
    assert report.r_multiples[0].r_multiple == D("0.68")
    assert report.reconciliation.alert_required is False


def test_sell_profit_has_correct_sign_and_reconciliation_mismatch_alerts() -> None:
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    report = PnlEngine.calculate(
        [
            PnlTrade(
                trade_id="short-1",
                epic="XAUUSD",
                direction=Direction.SELL,
                size=D("1"),
                entry_price=D("50"),
                stop_level=D("55"),
                value_per_point=D("2"),
                notional_exposure=D("200"),
                margin_used=D("25"),
                mark_price=D("47"),
            )
        ],
        starting_balance=D("1000"),
        currency="USD",
        broker=_broker(balance="1000", equity="1005.98", margin="25", exposure="200"),
        as_of=now,
    )

    assert report.unrealized_gross == D("6")
    assert report.equity == D("1006")
    assert report.reconciliation.equity_delta == D("0.02")
    assert report.reconciliation.alert_required is True


def test_rejects_duplicate_trade_ids_and_currency_mismatch() -> None:
    trade = PnlTrade(
        trade_id="duplicate",
        epic="NAS100",
        direction=Direction.BUY,
        size=D("1"),
        entry_price=D("100"),
        stop_level=D("95"),
        value_per_point=D("1"),
        notional_exposure=D("0"),
        margin_used=D("0"),
        exit_price=D("101"),
    )
    args = {
        "starting_balance": D("1000"),
        "currency": "USD",
        "broker": _broker(balance="1001", equity="1001"),
        "as_of": datetime(2026, 10, 5, 12, tzinfo=UTC),
    }
    with pytest.raises(ValueError, match="unique"):
        PnlEngine.calculate([trade, trade], **args)
    with pytest.raises(ValueError, match="currencies"):
        PnlEngine.calculate([trade], **{**args, "broker": _broker_for_currency("EUR")})


def _broker_for_currency(currency: str) -> BrokerAccountFigures:
    return BrokerAccountFigures(
        balance=D("1001"),
        equity=D("1001"),
        currency=currency,
        margin_used=D("0"),
        notional_exposure=D("0"),
    )


def test_open_trade_requires_mark_price_and_explicit_exposure_inputs() -> None:
    with pytest.raises(ValueError, match="mark_price"):
        PnlTrade(
            trade_id="open",
            epic="NAS100",
            direction=Direction.BUY,
            size=D("1"),
            entry_price=D("100"),
            stop_level=D("95"),
            value_per_point=D("1"),
            notional_exposure=D("100"),
            margin_used=D("10"),
        )
    with pytest.raises(TypeError):
        PnlTrade(
            trade_id="missing-inputs",
            epic="NAS100",
            direction=Direction.BUY,
            size=D("1"),
            entry_price=D("100"),
            stop_level=D("95"),
            value_per_point=D("1"),
            mark_price=D("101"),
        )
