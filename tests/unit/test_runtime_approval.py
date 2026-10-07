# ruff: noqa: E501
"""Approval and execution adapters keep the agent graph fail closed."""

from __future__ import annotations

import asyncio
from dataclasses import replace
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
from trader.runtime.approval import (
    GuardianInputs,
    PnlLossGuard,
    ProtectivePositionManager,
    RuntimeApproval,
    RuntimeContextProvider,
    RuntimeExecutor,
    RuntimeReconciler,
)


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


def test_context_provider_reads_broker_truth_and_sizing() -> None:
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
            }

        def get_market_details(self, _instrument: str) -> dict[str, object]:
            return {
                "latest_data_at": datetime(2026, 10, 7, 12, tzinfo=UTC),
                "market_open": True,
                "in_break": False,
                "bid": "99.9",
                "ask": "100.1",
                "atr": "1",
            }

        def get_positions(self) -> tuple[object, ...]:
            return ()

    provider = RuntimeContextProvider(
        Broker(),
        sizing_provider=lambda *_args: SizingEstimate(
            Decimal("1"), Decimal("10"), Decimal("10"), Decimal("100")
        ),
    )
    context = provider("cycle-1", _intent())
    assert context is not None
    assert context.market.ask == Decimal("100.1")


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


def test_executor_labels_pause_and_invalid_order_separately() -> None:
    class Manager:
        def __init__(self, error: Exception) -> None:
            self.error = error

        def submit(self, _cycle_id: str, _order: OrderRequest) -> object:
            raise self.error

    from trader.execution.manager import ExecutionPaused

    paused = asyncio.run(
        RuntimeExecutor(Manager(ExecutionPaused("paused"))).execute(
            _intent(), GuardianDecision(approved=True, approved_size=Decimal("1")), "cycle-1"
        )
    )
    invalid = asyncio.run(
        RuntimeExecutor(Manager(ValueError("invalid"))).execute(
            _intent(), GuardianDecision(approved=True, approved_size=Decimal("1")), "cycle-2"
        )
    )
    assert paused.reason == "execution_paused"
    assert invalid.reason == "order_invalid"


def test_pnl_guard_persists_baseline_and_pauses_on_restart() -> None:
    class Broker:
        equity = Decimal("100")

        def get_account(self) -> dict[str, Decimal]:
            return {"equity": self.equity}

    class Journal:
        def __init__(self) -> None:
            self.snapshot: dict[str, object] | None = None

        def get_snapshot(self, _snapshot_id: str) -> dict[str, object] | None:
            return self.snapshot

        def save_snapshot(self, _snapshot_id: str, payload: dict[str, object]) -> None:
            self.snapshot = payload

    class Writer:
        def __init__(self) -> None:
            self.mode = None

        def set(self, mode: object) -> None:
            self.mode = mode

    broker, journal, writer, alerts = Broker(), Journal(), Writer(), []
    first = PnlLossGuard(
        broker,
        max_daily_loss_pct=Decimal("2"),
        max_weekly_loss_pct=Decimal("5"),
        control_writer=writer,
        alert=alerts.append,
        journal=journal,
    )
    first(())
    broker.equity = Decimal("97")
    restarted = PnlLossGuard(
        broker,
        max_daily_loss_pct=Decimal("2"),
        max_weekly_loss_pct=Decimal("5"),
        control_writer=writer,
        alert=alerts.append,
        journal=journal,
    )
    import pytest

    with pytest.raises(RuntimeError, match="pnl_limit_daily"):
        restarted(())
    assert writer.mode is not None
    assert alerts == ["pnl_limit_daily"]


def test_position_manager_and_reconciler_alert_on_safety_mismatch() -> None:
    class Writer:
        def __init__(self) -> None:
            self.calls = 0

        def set(self, _mode: object) -> None:
            self.calls += 1

    writer, alerts = Writer(), []
    manager = ProtectivePositionManager(object(), control_writer=writer, alert=alerts.append)
    import pytest

    from trader.execution.brokers.base import Position

    position = Position("deal", "NAS100", Direction.BUY, Decimal("1"), Decimal("100"), None, None)
    with pytest.raises(RuntimeError, match="position_without_server_stop"):
        manager((position,))
    assert writer.calls == 1 and alerts == ["position_without_server_stop"]

    class Journal:
        def records(self) -> tuple[dict[str, object], ...]:
            return ()

    class Broker:
        def get_positions(self) -> tuple[Position, ...]:
            return (position,)

    reconciler = RuntimeReconciler(Journal(), Broker(), alert=alerts.append)
    assert not reconciler.reconcile()
    assert alerts[-1] == "broker_journal_mismatch"


def test_real_agent_graph_paper_fixture_routes_one_trade_through_order_manager(tmp_path) -> None:
    """The offline graph can submit only after the real Guardian approves it."""
    import json

    from sqlalchemy import create_engine

    from trader.agents.graph import AgentGraph, CycleInput
    from trader.agents.llm.client import LLMClient
    from trader.agents.llm.ledger import SqlUsageLedger
    from trader.agents.llm.providers import MockLLM
    from trader.agents.llm.types import ProviderReply
    from trader.core.audit import AuditEvent
    from trader.core.db import tables
    from trader.execution.brokers.paper import PaperBroker, PaperExecutionCosts
    from trader.execution.control import ControlMode, ControlUnavailable
    from trader.execution.journal import ExecutionJournal
    from trader.execution.manager import OrderManager
    from trader.risk.guardian import RiskGuardian, RiskLimits
    from trader.runtime.approval import PnlLossGuard, ProtectivePositionManager

    class Audit:
        def __init__(self) -> None:
            self.events: list[AuditEvent] = []

        def append(self, event: AuditEvent) -> None:
            self.events.append(event)

    class Lease:
        def acquire(self) -> None: ...

        def check(self) -> None: ...

    class Controls:
        def __init__(self) -> None:
            self.available = True

        def read(self) -> ControlMode:
            if not self.available:
                raise ControlUnavailable("redis unavailable")
            return ControlMode.RUN

        def set(self, _mode: ControlMode) -> None: ...

    engine = create_engine(f"sqlite:///{tmp_path / 'graph.db'}")
    tables["trade_intents"].create(engine)
    tables["snapshots"].create(engine)
    journal = ExecutionJournal(engine)
    audit = Audit()
    broker = PaperBroker(
        starting_cash=Decimal("10000"),
        currency="USD",
        costs=PaperExecutionCosts(
            spread_points={"NAS100": Decimal("1")},
            slippage_points={"NAS100": Decimal("0")},
        ),
    )
    limits = RiskLimits(
        risk_per_trade_pct=Decimal("0.5"),
        max_daily_loss_pct=Decimal("2"),
        max_weekly_loss_pct=Decimal("5"),
        max_drawdown_pct=Decimal("10"),
        max_open_positions_total=3,
        max_open_positions_per_instrument=2,
        max_combined_risk_pct=Decimal("1"),
        max_margin_usage_pct=Decimal("40"),
        max_effective_leverage=Decimal("5"),
        min_reward_risk=Decimal("1.5"),
        min_stop_atr=Decimal("0.5"),
        max_stop_atr=Decimal("3"),
        max_spread_atr_ratio=Decimal("0.2"),
        stale_data_max_seconds=20,
        max_orders_per_hour=600,
        blackout_before_min=30,
        blackout_after_min=15,
        consecutive_loss_pause=4,
        loss_cooldown_minutes=30,
        min_trade_quality_score=Decimal("0.7"),
    )
    guardian = RiskGuardian(limits, audit, canonical_symbols={"NAS100"})
    context = _inputs()
    approval = RuntimeApproval(guardian, context_provider=lambda *_args: context)
    controls = Controls()
    manager = OrderManager(
        broker,
        journal,
        Lease(),
        Lease(),
        approval,
        PnlLossGuard(
            broker,
            max_daily_loss_pct=Decimal("2"),
            max_weekly_loss_pct=Decimal("5"),
            control_writer=Controls(),
            alert=lambda _message: None,
            journal=journal,
        ),
        lambda _message: None,
        controls,
        controls,
        ProtectivePositionManager(broker, control_writer=Controls(), alert=lambda _message: None),
    )
    manager.startup()

    def reply(value: dict[str, object]) -> ProviderReply:
        return ProviderReply(json.dumps(value), 1, 1)

    responses = [
        reply({"items": [], "flags": []}),
        reply(
            {
                "bias": "long",
                "confidence": 0.9,
                "regime": "trend",
                "timeframe_agreement": "aligned",
                "key_levels": [],
                "risks": [],
                "flags": [],
            }
        ),
        reply(
            {"event_risk": "none", "upcoming_events": [], "backdrop": [], "claims": [], "flags": []}
        ),
        reply(
            {
                "action": "TRADE",
                "intent": {
                    "instrument": "NAS100",
                    "direction": "BUY",
                    "entry_type": "market",
                    "entry_level": "100",
                    "stop_level": "99",
                    "target_levels": ["102"],
                    "time_horizon": "intraday",
                    "thesis": "fixture",
                    "confidence": 0.9,
                },
                "thesis": "fixture",
                "confidence": 0.9,
                "no_trade_reason": None,
            }
        ),
        reply({"verdict": "approve", "objections": [], "strongest_reason": "", "flags": []}),
    ]
    llm = MockLLM(responses)
    llm_config = {
        "tiers": {
            "cheap": {
                "provider": "cheap",
                "model": "cheap",
                "max_input_tokens": 1000,
                "max_output_tokens": 1000,
                "input_usd_per_million": 1,
                "output_usd_per_million": 1,
            }
        },
        "roles": {
            role: "cheap"
            for role in (
                "data_summarizer",
                "technical_analyst",
                "news_macro_analyst",
                "strategist",
                "critic",
            )
        },
        "fallback": {},
        "health_probe": {
            "interval_minutes": 15,
            "timeout_seconds": 1,
            "failures_to_open": 3,
            "cooldown_minutes": 5,
        },
    }
    client = LLMClient(
        config=llm_config,
        providers={"cheap": llm},
        ledger=SqlUsageLedger(engine, daily_cap_usd=Decimal("4"), monthly_cap_usd=Decimal("80")),
    )
    graph = AgentGraph(
        llm=client, audit=audit, guardian=approval, executor=RuntimeExecutor(manager)
    )
    result = asyncio.run(
        graph.run(
            CycleInput(
                cycle_id="cycle-1",
                event="bar_close",
                event_id="event-1",
                instrument="NAS100",
                snapshot_json='{"price":100}',
                research=(),
                guardian_context={},
            )
        )
    )
    assert result.action == "TRADE", (
        result,
        [(e.event_type, getattr(e, "payload", None)) for e in audit.events],
    )
    assert len(broker.get_positions()) == 1
    assert broker.get_positions()[0].stop_level == Decimal("99")
    assert any(event.event_type == "risk.decision" for event in audit.events)
    assert all(event.cycle_id == "cycle-1" for event in audit.events)

    # A Guardian rejection must not reserve or submit another order.
    context = replace(context, account=replace(context.account, paused=True))
    rejected_graph = AgentGraph(
        llm=LLMClient(
            config=llm_config,
            providers={"cheap": MockLLM(responses)},
            ledger=SqlUsageLedger(
                engine, daily_cap_usd=Decimal("4"), monthly_cap_usd=Decimal("80")
            ),
        ),
        audit=audit,
        guardian=approval,
        executor=RuntimeExecutor(manager),
    )
    rejected = asyncio.run(
        rejected_graph.run(
            CycleInput(
                cycle_id="cycle-2",
                event="bar_close",
                event_id="event-2",
                instrument="NAS100",
                snapshot_json='{"price":100}',
                research=(),
                guardian_context={},
            )
        )
    )
    assert rejected.action == "NO_TRADE"
    assert rejected.reason == "guardian_rejected"
    assert len(broker.get_positions()) == 1

    # A Redis/control outage fails closed before the broker is called.
    context = replace(context, account=replace(context.account, paused=False))
    controls.available = False
    blocked_graph = AgentGraph(
        llm=LLMClient(
            config=llm_config,
            providers={"cheap": MockLLM(responses)},
            ledger=SqlUsageLedger(
                engine, daily_cap_usd=Decimal("4"), monthly_cap_usd=Decimal("80")
            ),
        ),
        audit=audit,
        guardian=approval,
        executor=RuntimeExecutor(manager),
    )
    blocked = asyncio.run(
        blocked_graph.run(
            CycleInput(
                cycle_id="cycle-3",
                event="bar_close",
                event_id="event-3",
                instrument="NAS100",
                snapshot_json='{"price":100}',
                research=(),
                guardian_context={},
            )
        )
    )
    assert blocked.action == "NO_TRADE"
    assert blocked.execution is not None
    assert blocked.execution.reason == "execution_paused"
    assert len(broker.get_positions()) == 1
