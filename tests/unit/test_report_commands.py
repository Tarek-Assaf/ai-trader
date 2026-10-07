from __future__ import annotations

from decimal import Decimal

from trader.execution.brokers.base import Direction, Position
from trader.reports.commands import CommandDataService


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
