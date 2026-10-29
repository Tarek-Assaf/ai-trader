from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from trader.execution.brokers.base import Direction, Position
from trader.execution.brokers.paper import PaperBroker, PaperExecutionCosts
from trader.reports.commands import CommandDataService, ScheduledReportRunner


class Broker:
    def get_account(self) -> dict[str, object]:
        return {
            "currency": "USD",
            "balance": Decimal("1000"),
            "equity": Decimal("1012"),
            "available": Decimal("900"),
            "profitLoss": Decimal("12"),
        }

    def get_positions(self) -> tuple[Position, ...]:
        return (
            Position(
                "deal-1",
                "NAS100",
                Direction.BUY,
                Decimal("1"),
                Decimal("100"),
                Decimal("95"),
                None,
            ),
        )


def test_commands_use_current_fixture_data_without_llm() -> None:
    service = CommandDataService(
        Broker(),
        pnl_provider=lambda: {"realized": "USD 12"},
        risk_provider=lambda: {
            "mode": "run",
            "limits": {"risk_per_trade_pct": Decimal("0.5")},
        },
    )
    assert "equity 1012" in service.status()
    assert "NAS100 BUY" in service.positions()
    assert "USD 12" in service.pnl()
    assert "mode: run" in service.risk()
    assert "risk_per_trade: 0.5%" in service.risk()


def test_missing_provider_fails_closed_and_does_not_invent_numbers() -> None:
    service = CommandDataService()
    assert service.status().endswith("unavailable right now.")
    assert service.pnl().endswith("unavailable right now.")
    assert service.risk().endswith("unavailable right now.")
    assert service.report().endswith("unavailable right now.")


def test_real_paper_broker_renders_pnl_and_readable_risk_summary() -> None:
    broker = PaperBroker(
        starting_cash=Decimal("10000"),
        currency="USD",
        costs=PaperExecutionCosts(
            spread_points={"US100": Decimal("2")},
            slippage_points={"US100": Decimal("1")},
        ),
    )
    service = CommandDataService(
        broker,
        risk_provider=lambda: {
            "mode": "paper",
            "limits": {
                "max_daily_loss_pct": Decimal("2"),
                "max_weekly_loss_pct": Decimal("5"),
                "max_drawdown_pct": Decimal("10"),
                "risk_per_trade_pct": Decimal("0.5"),
                "max_open_positions_total": 3,
                "max_orders_per_hour": 600,
            },
        },
    )

    assert service.pnl() == "P&L: USD 0."
    risk = service.risk()
    assert "mode: paper" in risk
    assert "risk_per_trade: 0.5%" in risk
    assert "{" not in risk
    assert len(risk.splitlines()) < 12


def test_chart_returns_png_bytes_from_fixture_equity_data() -> None:
    from datetime import UTC, datetime

    from trader.execution.pnl import BrokerAccountFigures, EquityPoint, PnlEngine

    now = datetime(2026, 10, 7, tzinfo=UTC)
    account = BrokerAccountFigures(
        balance=Decimal("1000"),
        equity=Decimal("1000"),
        currency="USD",
        margin_used=Decimal("0"),
        notional_exposure=Decimal("0"),
    )
    pnl = PnlEngine.calculate(
        [],
        starting_balance=Decimal("1000"),
        currency="USD",
        broker=account,
        as_of=now,
        equity_history=(EquityPoint(now, Decimal("999")),),
    )
    png = CommandDataService(pnl_provider=lambda: pnl).chart("/chart")
    assert png.startswith(b"\x89PNG")


def test_report_render_does_not_send_duplicate_telegram_message() -> None:
    from test_reports import _FakeTelegram, _snapshot

    transport = _FakeTelegram()
    runner = ScheduledReportRunner(
        {},
        lambda period: replace(_snapshot(), period=period),
        telegram=transport,
        owner_chat_ids=("owner",),
    )
    assert runner.render("daily") is not None
    assert transport.messages == []
    assert runner.run("daily") is not None
    assert len(transport.messages) == 1


def test_scheduled_report_includes_llm_cost_from_usage_summary() -> None:
    from test_reports import _snapshot

    runner = ScheduledReportRunner(
        {},
        lambda period: replace(_snapshot(), period=period),
        usage_summary=lambda: {"cost_usd": Decimal("1.2345")},
    )
    document = runner.render("daily")
    assert document is not None
    assert "Spend: USD 1.23" in document.plain_text


def test_pnl_shows_closed_open_total_and_trade_count_one_per_line() -> None:
    from types import SimpleNamespace

    report = SimpleNamespace(
        currency="USD",
        realized_net=Decimal("-13.20"),
        unrealized_net=Decimal("0"),
        balance=Decimal("10965.50"),
        equity=Decimal("10965.50"),
        r_multiples=(
            SimpleNamespace(net_profit=Decimal("-11.79")),
            SimpleNamespace(net_profit=Decimal("-2.41")),
            SimpleNamespace(net_profit=Decimal("1.00")),
        ),
    )

    text = CommandDataService(pnl_provider=lambda: report).pnl()

    assert text.splitlines() == [
        "P&L today (since 03:00 Riyadh):",
        "Closed trades: -$13.20",
        "Open positions: +$0.00",
        "Total: -$13.20",
        "Balance: 10965.50",
        "Equity: 10965.50",
        "Trades closed: 3 (1 won, 2 lost)",
    ]


def test_pnl_without_trades_says_none_yet() -> None:
    from types import SimpleNamespace

    report = SimpleNamespace(
        currency="USD",
        realized_net=Decimal("0"),
        unrealized_net=Decimal("0"),
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        r_multiples=(),
    )

    assert "Trades closed: none yet" in CommandDataService(pnl_provider=lambda: report).pnl()
