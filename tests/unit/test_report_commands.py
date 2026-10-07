from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from trader.execution.brokers.base import Direction, Position
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
        risk_provider=lambda: {"mode": "run", "daily_limit": "2%"},
    )
    assert "equity 1012" in service.status()
    assert "NAS100 BUY" in service.positions()
    assert "USD 12" in service.pnl()
    assert "daily_limit=2%" in service.risk()


def test_missing_provider_fails_closed_and_does_not_invent_numbers() -> None:
    service = CommandDataService()
    assert service.status().endswith("unavailable right now.")
    assert service.pnl().endswith("unavailable right now.")
    assert service.risk().endswith("unavailable right now.")
    assert service.report().endswith("unavailable right now.")


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
    from tests.unit.test_reports import _FakeTelegram, _snapshot

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
    from tests.unit.test_reports import _snapshot

    runner = ScheduledReportRunner(
        {},
        lambda period: replace(_snapshot(), period=period),
        usage_summary=lambda: {"cost_usd": Decimal("1.2345")},
    )
    document = runner.render("daily")
    assert document is not None
    assert "1.2345" in document.plain_text
