"""Approval and execution adapters keep the agent graph fail closed."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from trader.agents.graph import GuardianDecision
from trader.agents.graph import TradeIntent as GraphTradeIntent
from trader.execution.brokers.base import Direction, OrderRequest
from trader.risk.guardian import (
    AccountState,
    MarketSnapshot,
    RiskDecision,
    SizingEstimate,
)
from trader.runtime.approval import GuardianInputs, RuntimeApproval, RuntimeExecutor


class Guardian:
    def __init__(self, decision: RiskDecision) -> None:
        self.decision = decision

    def evaluate(self, *_args: object, **_kwargs: object) -> RiskDecision:
        return self.decision


def _inputs() -> GuardianInputs:
    now = datetime(2026, 10, 7, 12, tzinfo=UTC)
    return GuardianInputs(
        market=MarketSnapshot(
            latest_data_at=now,
            market_open=True,
            in_break=False,
            bid=Decimal("99.9"),
            ask=Decimal("100.1"),
            atr=Decimal("1"),
        ),
        account=AccountState(
            equity=Decimal("10000"),
            day_start_equity=Decimal("10000"),
            week_start_equity=Decimal("10000"),
            peak_equity=Decimal("10000"),
            daily_pnl=Decimal("0"),
            weekly_pnl=Decimal("0"),
            margin_used=Decimal("0"),
            notional_exposure=Decimal("0"),
            orders_last_hour=0,
            consecutive_losses=0,
            last_loss_at=None,
        ),
        sizing=SizingEstimate(Decimal("1"), Decimal("10"), Decimal("10"), Decimal("100")),
        stage_size_cap=Decimal("2"),
        trade_quality_score=Decimal("0.9"),
        now=now,
    )


def _intent() -> GraphTradeIntent:
    return GraphTradeIntent(
        instrument="NAS100",
        direction="BUY",
        entry_type="market",
        entry_level=Decimal("100"),
        stop_level=Decimal("99"),
        target_levels=(Decimal("102"),),
        time_horizon="intraday",
        thesis="fixture",
        confidence=0.9,
    )


def test_approval_binds_exact_order_and_rejects_a_mutation() -> None:
    approval = RuntimeApproval(Guardian(RiskDecision(True, Decimal("1"), (), ())))
    decision = asyncio.run(approval.evaluate(_intent(), _inputs(), "cycle-1"))
    assert decision.approved and decision.approved_size == Decimal("1")

    exact = OrderRequest(
        "cycle-1",
        "NAS100",
        Direction.BUY,
        Decimal("1"),
        Decimal("100"),
        Decimal("99"),
        Decimal("102"),
    )
    assert approval(exact, "cycle-1").decision.approved
    changed = OrderRequest(
        "cycle-2",
        "NAS100",
        Direction.BUY,
        Decimal("1"),
        Decimal("100"),
        Decimal("99"),
        Decimal("102"),
    )
    assert not approval(changed, "cycle-1").decision.approved


def test_missing_context_cannot_approve() -> None:
    approval = RuntimeApproval(Guardian(RiskDecision(True, Decimal("1"), (), ())))
    decision = asyncio.run(approval.evaluate(_intent(), object(), "cycle-1"))
    assert not decision.approved
    assert "guardian_context_or_sizing_missing" in decision.reasons


def test_executor_only_calls_order_manager() -> None:
    class Manager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, OrderRequest]] = []

        def submit(self, cycle_id: str, order: OrderRequest) -> object:
            self.calls.append((cycle_id, order))
            return type("Receipt", (), {"deal_reference": "paper-open-1"})()

    manager = Manager()
    outcome = asyncio.run(
        RuntimeExecutor(manager).execute(
            _intent(), GuardianDecision(approved=True, approved_size=Decimal("1")), "cycle-1"
        )
    )
    assert outcome.status == "submitted"
    assert manager.calls[0][1].client_order_id == "cycle-1"
