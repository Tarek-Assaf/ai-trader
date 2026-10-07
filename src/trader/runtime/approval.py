"""Adapters that bind the typed agent graph to the deterministic execution path.

The agent graph and the phase 2 risk code intentionally use different models.
The graph speaks in proposals, while :class:`RiskGuardian` requires a fully
valued account and market context.  This module is the only translation
boundary.  It also keeps the Guardian decision bound to the exact order that
the OrderManager receives, so an agent cannot approve a changed order.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from trader.agents.graph import (
    ExecutionOutcome,
    GuardianDecision,
)
from trader.agents.graph import (
    TradeIntent as GraphTradeIntent,
)
from trader.execution.brokers.base import Direction, OrderRequest, Position
from trader.execution.control import ControlMode
from trader.execution.manager import BoundApproval, ExecutionPaused
from trader.risk.guardian import (
    AccountState,
    MarketSnapshot,
    OpenPosition,
    RiskDecision,
    RiskGuardian,
    SizingEstimate,
    TradeIntent,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GuardianInputs:
    """Trusted, deterministic inputs supplied to one graph cycle."""

    market: MarketSnapshot
    account: AccountState
    open_positions: tuple[OpenPosition, ...] = ()
    sizing: SizingEstimate | None = None
    stage_size_cap: Decimal | None = None
    trade_quality_score: Decimal | None = None
    now: datetime | None = None

    def timestamp(self) -> datetime:
        value = self.now or datetime.now(UTC)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Guardian timestamp must be timezone-aware")
        return value


def _reject(reason: str, *, details: Mapping[str, Any] | None = None) -> GuardianDecision:
    return GuardianDecision(approved=False, reasons=(reason,), details=dict(details or {}))


def _inputs(value: object) -> GuardianInputs | None:
    if isinstance(value, GuardianInputs):
        return value
    if not isinstance(value, Mapping):
        return None
    try:
        market = value["market"]
        account = value["account"]
        if not isinstance(market, MarketSnapshot) or not isinstance(account, AccountState):
            return None
        positions = value.get("open_positions", ())
        if not isinstance(positions, Sequence) or isinstance(positions, str | bytes):
            return None
        if any(not isinstance(position, OpenPosition) for position in positions):
            return None
        return GuardianInputs(
            market=market,
            account=account,
            open_positions=tuple(positions),
            sizing=value.get("sizing") if isinstance(value.get("sizing"), SizingEstimate) else None,
            stage_size_cap=value.get("stage_size_cap")
            if isinstance(value.get("stage_size_cap"), Decimal)
            else None,
            trade_quality_score=value.get("trade_quality_score")
            if isinstance(value.get("trade_quality_score"), Decimal)
            else None,
            now=value.get("now") if isinstance(value.get("now"), datetime) else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def legacy_intent(intent: GraphTradeIntent) -> TradeIntent:
    """Convert a graph proposal without allowing it to choose a size."""

    return TradeIntent(
        instrument=intent.instrument,
        direction="buy" if intent.direction == "BUY" else "sell",
        entry_price=intent.entry_level,
        stop_loss=intent.stop_level,
        take_profit=intent.target_levels[0] if intent.target_levels else None,
    )


def order_from_intent(
    intent: GraphTradeIntent,
    *,
    size: Decimal,
    cycle_id: str,
) -> OrderRequest:
    """Build the immutable execution request from a Guardian-approved proposal."""

    return OrderRequest(
        client_order_id=cycle_id,
        epic=intent.instrument,
        direction=Direction(intent.direction),
        size=size,
        reference_price=intent.entry_level,
        stop_level=intent.stop_level,
        profit_level=intent.target_levels[0] if intent.target_levels else None,
    )


class RuntimeApproval:
    """Async graph Guardian plus exact-order approval callback for OrderManager."""

    def __init__(
        self,
        guardian: RiskGuardian,
        *,
        context_provider: Callable[..., GuardianInputs | None] | None = None,
    ) -> None:
        self.guardian = guardian
        self._context_provider = context_provider
        self._bound: dict[str, BoundApproval] = {}

    def _context(
        self, cycle_id: str, supplied: object, intent: GraphTradeIntent
    ) -> GuardianInputs | None:
        value = _inputs(supplied)
        if value is not None:
            return value
        if self._context_provider is not None:
            try:
                return self._context_provider(cycle_id, intent)
            except TypeError:
                return self._context_provider(cycle_id)
        return None

    async def evaluate(
        self, intent: GraphTradeIntent, context: object, cycle_id: str
    ) -> GuardianDecision:
        inputs = self._context(cycle_id, context, intent)
        if inputs is None or inputs.sizing is None:
            return _reject("guardian_context_or_sizing_missing")
        try:
            decision = self.guardian.evaluate(
                legacy_intent(intent),
                inputs.market,
                inputs.account,
                inputs.open_positions,
                inputs.sizing,
                stage_size_cap=inputs.stage_size_cap,
                trade_quality_score=inputs.trade_quality_score,
                now=inputs.timestamp(),
                cycle_id=cycle_id,
            )
        except Exception:
            LOGGER.exception("Guardian evaluation failed; rejecting cycle")
            return _reject("guardian_evaluation_failed")
        if decision.approved and decision.approved_size is not None:
            try:
                order = order_from_intent(intent, size=decision.approved_size, cycle_id=cycle_id)
                self._bound[cycle_id] = BoundApproval(order, cycle_id, decision)
            except (TypeError, ValueError):
                return _reject("guardian_order_binding_failed")
        return GuardianDecision(
            approved=decision.approved,
            approved_size=decision.approved_size,
            reasons=decision.reasons,
            details={
                "rules_checked": decision.rules_checked,
                "halt_system": decision.halt_system,
                "audit_written": decision.audit_written,
            },
        )

    def __call__(self, order: OrderRequest, cycle_id: str) -> BoundApproval:
        bound = self._bound.pop(cycle_id, None)
        if bound is None or bound.order != order:
            return BoundApproval(
                order,
                cycle_id,
                RiskDecision(
                    approved=False,
                    approved_size=None,
                    reasons=("guardian_order_binding_missing",),
                    rules_checked=(),
                ),
            )
        return bound


class RuntimeExecutor:
    """Graph executor that can only submit through the OrderManager."""

    def __init__(self, order_manager: Any) -> None:
        self.order_manager = order_manager

    async def execute(
        self,
        intent: GraphTradeIntent,
        decision: GuardianDecision,
        cycle_id: str,
    ) -> ExecutionOutcome:
        if not decision.approved or decision.approved_size is None:
            return ExecutionOutcome(status="rejected", reason="guardian_rejected")
        try:
            order = order_from_intent(intent, size=decision.approved_size, cycle_id=cycle_id)
            receipt = self.order_manager.submit(cycle_id, order)
            if receipt is None:
                return ExecutionOutcome(status="rejected", reason="duplicate_cycle")
            return ExecutionOutcome(status="submitted", reference=receipt.deal_reference)
        except ExecutionPaused:
            return ExecutionOutcome(status="failed", reason="execution_paused")
        except ValueError:
            return ExecutionOutcome(status="failed", reason="order_invalid")
        except RuntimeError:
            return ExecutionOutcome(status="failed", reason="execution_failed")
        except Exception:
            LOGGER.exception("execution adapter failed closed")
            return ExecutionOutcome(status="failed", reason="execution_failed")


class PnlLossGuard:
    """Deterministic daily/weekly loss gate used by OrderManager reconciliation."""

    def __init__(
        self,
        broker: Any,
        *,
        max_daily_loss_pct: Decimal,
        max_weekly_loss_pct: Decimal,
        control_writer: Any | None,
        alert: Callable[[str], None],
        journal: object | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.broker = broker
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_weekly_loss_pct = max_weekly_loss_pct
        self.control_writer = control_writer
        self.alert = alert
        self.journal = journal
        self.clock = clock
        self._day_baseline: Decimal | None = None
        self._week_baseline: Decimal | None = None
        self._peak_equity: Decimal | None = None
        self._baseline_day: str | None = None
        self._baseline_week: str | None = None

    def _load_or_store_baseline(self, equity: Decimal) -> tuple[Decimal, Decimal]:
        now = self.clock().astimezone(UTC)
        day = now.date().isoformat()
        iso = now.date().isocalendar()
        week = f"{iso.year}-W{iso.week:02d}"
        snapshot = None
        getter = getattr(self.journal, "get_snapshot", None)
        if self._day_baseline is None and callable(getter):
            snapshot = getter("runtime:pnl-baseline")
        if isinstance(snapshot, Mapping):
            try:
                self._day_baseline = Decimal(
                    str(snapshot.get("day_equity") or snapshot["day_start_equity"])
                )
                self._week_baseline = Decimal(
                    str(snapshot.get("week_equity") or snapshot["week_start_equity"])
                )
                self._peak_equity = Decimal(str(snapshot.get("peak_equity", snapshot["week_equity"])))
                self._baseline_day = str(snapshot["day"])
                self._baseline_week = str(snapshot["week"])
            except (ArithmeticError, KeyError, TypeError, ValueError):
                self._day_baseline = None
                self._week_baseline = None
                self._peak_equity = None
        if self._day_baseline is None or self._baseline_day != day:
            self._day_baseline = equity
            self._baseline_day = day
        if self._week_baseline is None or self._baseline_week != week:
            self._week_baseline = equity
            self._baseline_week = week
        self._peak_equity = max(self._peak_equity or equity, equity)
        saver = getattr(self.journal, "save_snapshot", None)
        if callable(saver):
            saver(
                "runtime:pnl-baseline",
                {
                    "day_equity": str(self._day_baseline),
                    "week_equity": str(self._week_baseline),
                    "peak_equity": str(self._peak_equity),
                    "day": self._baseline_day,
                    "week": self._baseline_week,
                },
            )
        assert self._day_baseline is not None and self._week_baseline is not None
        return self._day_baseline, self._week_baseline

    def __call__(self, _positions: Sequence[Position]) -> None:
        account = self.broker.get_account()
        raw_equity = (
            account.get("equity", account.get("balance")) if isinstance(account, Mapping) else None
        )
        try:
            equity = Decimal(str(raw_equity))
        except (ArithmeticError, TypeError, ValueError):
            equity = Decimal("NaN")
        if not equity.is_finite() or equity <= 0:
            raise RuntimeError("account equity is unavailable")
        day_baseline, week_baseline = self._load_or_store_baseline(equity)
        daily = account.get("daily_pnl") if isinstance(account, Mapping) else None
        weekly = account.get("weekly_pnl") if isinstance(account, Mapping) else None
        try:
            daily_pnl = Decimal(str(daily)) if daily is not None else equity - day_baseline
            weekly_pnl = Decimal(str(weekly)) if weekly is not None else equity - week_baseline
        except (ArithmeticError, TypeError, ValueError):
            raise RuntimeError("account P&L is unavailable") from None
        breached = []
        for name, pnl, baseline, limit in (
            ("daily", daily_pnl, day_baseline, self.max_daily_loss_pct),
            ("weekly", weekly_pnl, week_baseline, self.max_weekly_loss_pct),
        ):
            if not isinstance(pnl, Decimal) or pnl.is_nan() or pnl.is_infinite():
                raise RuntimeError(f"{name} P&L is unavailable")
            if max(Decimal(0), -pnl) / baseline * 100 >= limit:
                breached.append(name)
        if breached:
            if self.control_writer is not None:
                self.control_writer.set(ControlMode.PAUSE)
            reason = "pnl_limit_" + "_and_".join(breached)
            self.alert(reason)
            raise RuntimeError(reason)


class ProtectivePositionManager:
    """Keep every broker position protected; never creates a new entry."""

    def __init__(
        self, broker: Any, *, control_writer: Any | None, alert: Callable[[str], None]
    ) -> None:
        self.broker = broker
        self.control_writer = control_writer
        self.alert = alert

    def __call__(self, positions: Sequence[Position]) -> None:
        for position in positions:
            if position.stop_level is not None:
                continue
            if self.control_writer is not None:
                self.control_writer.set(ControlMode.PAUSE)
            self.alert("position_without_server_stop")
            raise RuntimeError("position_without_server_stop")


class RuntimeReconciler:
    """Small read-model reconciler used alongside OrderManager's hard gate."""

    def __init__(self, journal: Any, broker: Any, *, alert: Callable[[str], None]) -> None:
        self.journal = journal
        self.broker = broker
        self.alert = alert

    def reconcile(self) -> bool:
        try:
            positions = tuple(self.broker.get_positions())
            records = tuple(self.journal.records())
            known = {record.get("deal_id") for record in records if record.get("deal_id")}
            valid = all(
                position.deal_id in known and position.stop_level is not None
                for position in positions
            )
        except Exception:
            valid = False
        if not valid:
            self.alert("broker_journal_mismatch")
        return valid

    def reconcile_pnl(
        self,
        trades: Sequence[Any],
        *,
        starting_balance: Decimal,
        currency: str,
        broker: Any | None = None,
        tolerance: Decimal = Decimal("0.01"),
    ) -> object:
        """Delegate P&L math while keeping this reconciler's journal attached."""

        from trader.execution.reconciliation import reconcile_pnl

        account = broker or self.broker
        figures = account.get_account()
        from trader.execution.pnl import BrokerAccountFigures

        if not isinstance(figures, Mapping):
            raise ValueError("broker account figures are unavailable")
        normalized = BrokerAccountFigures(
            balance=Decimal(str(figures["balance"])),
            equity=Decimal(str(figures["equity"])),
            currency=str(figures.get("currency", currency)),
            margin_used=Decimal(str(figures.get("margin_used", "0"))),
            notional_exposure=Decimal(str(figures.get("notional_exposure", "0"))),
        )
        return reconcile_pnl(
            trades,
            self.journal,
            starting_balance=starting_balance,
            currency=currency,
            broker=normalized,
            tolerance=tolerance,
        )


__all__ = [
    "GuardianInputs",
    "PnlLossGuard",
    "ProtectivePositionManager",
    "RuntimeApproval",
    "RuntimeExecutor",
    "RuntimeReconciler",
    "legacy_intent",
    "order_from_intent",
]
