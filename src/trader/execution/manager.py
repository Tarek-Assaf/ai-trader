"""Deterministic order submission with durable at-most-once intent reservation."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from trader.execution.brokers.base import BrokerAdapter, OrderReceipt, OrderRequest, Position
from trader.execution.control import (
    ControlMode,
    ControlSource,
    ControlUnavailable,
    ControlWriter,
)
from trader.execution.journal import ExecutionJournal, restored_order
from trader.risk.guardian import RiskDecision


class Ownership(Protocol):
    def acquire(self) -> None: ...

    def check(self) -> None: ...


@dataclass(frozen=True, slots=True)
class BoundApproval:
    """Guardian decision bound to every immutable order field and cycle."""

    order: OrderRequest
    cycle_id: str
    decision: RiskDecision


class ExecutionPaused(RuntimeError):
    """No entry is allowed until reconciliation and explicit recovery succeed."""


class OrderManager:
    """Sole application submission boundary; never retry reserved intents.

    Guardian evaluation happens here under its own Redis service lease. The
    callback must translate the supplied exact order into the deterministic
    Guardian inputs, using current reconciled account and sizing information.
    P&L checking is mandatory and exceptions pause execution before approval.
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        journal: ExecutionJournal,
        execution_lease: Ownership,
        guardian_lease: Ownership,
        approve: Callable[[OrderRequest, str], BoundApproval],
        check_pnl: Callable[[Sequence[Position]], None],
        alert: Callable[[str], None],
        control_source: ControlSource,
        control_writer: ControlWriter,
        manage_positions: Callable[[Sequence[Position]], None],
        *,
        reconcile_interval_seconds: int = 5,
    ) -> None:
        if reconcile_interval_seconds <= 0:
            raise ValueError("reconciliation interval must be positive")
        self._broker = broker
        self._journal = journal
        self._execution_lease = execution_lease
        self._guardian_lease = guardian_lease
        self._approve = approve
        self._check_pnl = check_pnl
        self._alert = alert
        self._interval = timedelta(seconds=reconcile_interval_seconds)
        self._control_source = control_source
        self._control_writer = control_writer
        self._manage_positions = manage_positions
        self._last_reconcile: datetime | None = None
        self._positions: tuple[Position, ...] = ()
        self._paused = True
        self._started = False
        self._mutex = threading.RLock()
        self._kill_alerted = False
        self._external_pause_latched = False

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def positions(self) -> tuple[Position, ...]:
        return self._positions

    def _pause(self, reason: str) -> None:
        self._paused = True
        try:
            self._journal.save_pause(reason)
        except Exception:
            pass
        try:
            self._alert(reason)
        except Exception:
            # A broken notification channel cannot permit a new order.
            pass

    def startup(self) -> None:
        with self._mutex:
            try:
                self._execution_lease.acquire()
                self._guardian_lease.acquire()
                self._started = True
                mode = self._read_controls()
                if mode is ControlMode.KILL or self._journal.is_killed():
                    self._apply_controls()
                    return
                if not self.reconcile():
                    return
                mode = self._apply_controls()
                if mode is not ControlMode.RUN:
                    return
                if not self._journal.is_paused() and not self._journal.is_killed():
                    self._paused = False
            except Exception:
                self._pause("execution_startup_failed")
                raise ExecutionPaused("execution startup failed") from None

    def reconcile(self) -> bool:
        """Poll broker truth and heal the read model; a mismatch stays paused."""
        with self._mutex:
            try:
                self._execution_lease.check()
                self._guardian_lease.check()
                positions = tuple(self._broker.get_positions())
                if len({position.deal_id for position in positions}) != len(positions):
                    raise ValueError("duplicate broker deal ids")
                working_orders = self._broker.get_open_orders()
                records = self._journal.records()
                known = {record["deal_id"]: record for record in records if record.get("deal_id")}
                mismatch = bool(working_orders)
                for position in positions:
                    record = known.get(position.deal_id)
                    if record is None:
                        mismatch = True
                        continue
                    order = restored_order(record["execution_order"])
                    safe_stop = position.stop_level is not None and (
                        position.stop_level >= order.stop_level
                        if order.direction.value == "BUY"
                        else position.stop_level <= order.stop_level
                    )
                    if (
                        position.epic != order.epic
                        or position.direction != order.direction
                        or position.size <= 0
                        or position.size > order.size
                        or not safe_stop
                    ):
                        mismatch = True
                active_ids = {position.deal_id for position in positions}
                for record in records:
                    state = record["execution_state"]
                    if state in {"reserved", "ambiguous"}:
                        # Position absence alone cannot prove an order was rejected.
                        self._broker.get_activity(
                            datetime.now(UTC) - timedelta(days=1), datetime.now(UTC)
                        )
                        mismatch = True
                    elif state == "submitted" and record["deal_id"] not in active_ids:
                        self._journal.mark(record["execution_order"]["client_order_id"], "closed")
                self._positions = positions
                self._check_pnl(positions)
                self._last_reconcile = datetime.now(UTC)
                if mismatch:
                    self._pause("broker_state_mismatch")
                return not mismatch
            except Exception:
                self._pause("broker_reconciliation_failed")
                raise ExecutionPaused("reconciliation failed") from None

    def tick(self) -> None:
        """Check emergency controls and service open positions on every loop."""
        with self._mutex:
            try:
                self._execution_lease.check()
            except Exception:
                self._pause("execution_lease_lost")
                return
            mode = self._apply_controls()
            if mode is ControlMode.KILL:
                return
            try:
                # The injected routine may manage existing stops but must not create entries.
                self._manage_positions(self._positions)
            except Exception:
                self._pause("position_management_failed")
                return
            if (
                self._last_reconcile is None
                or datetime.now(UTC) - self._last_reconcile >= self._interval
            ):
                self.reconcile()

    def resume(self) -> None:
        """Explicit owner recovery after controls clear and broker state is safe."""
        with self._mutex:
            if not self._started:
                raise ExecutionPaused("execution has not started")
            try:
                self._execution_lease.check()
                self._guardian_lease.check()
            except Exception:
                self._pause("execution_resume_lease_check_failed")
                raise ExecutionPaused("execution lease is unavailable") from None
            if self._control_writer is not None:
                try:
                    self._control_writer.set(ControlMode.RUN)
                except Exception:
                    raise ExecutionPaused("control source could not be cleared") from None
            mode = self._read_controls()
            if mode is not ControlMode.RUN:
                raise ExecutionPaused("an external pause or kill trigger is still active")
            was_killed = self._journal.is_killed()
            if was_killed and not self._flatten_everything():
                raise ExecutionPaused("kill is latched until all broker orders are closed")
            if not self.reconcile():
                raise ExecutionPaused("broker state is unresolved")
            if was_killed:
                self._journal.clear_kill()
            self._journal.clear_pause()
            self._paused = False
            self._external_pause_latched = False
            self._kill_alerted = False

    def _read_controls(self) -> ControlMode:
        if self._control_source is None:
            return ControlMode.RUN
        try:
            return self._control_source.read()
        except ControlUnavailable:
            self._pause("execution_control_unavailable")
            return ControlMode.PAUSE
        except Exception:
            self._pause("execution_control_unavailable")
            return ControlMode.PAUSE

    def _apply_controls(self) -> ControlMode:
        """Latch external controls; kill repeatedly attempts to flatten broker state."""
        mode = self._read_controls()
        if mode is ControlMode.RESUME:
            try:
                self.resume()
            except ExecutionPaused:
                try:
                    return ControlMode.KILL if self._journal.is_killed() else ControlMode.PAUSE
                except Exception:
                    return ControlMode.PAUSE
            return ControlMode.RUN
        try:
            killed = self._journal.is_killed()
        except Exception:
            self._pause("execution_control_state_unavailable")
            return ControlMode.PAUSE
        if mode is ControlMode.KILL or killed:
            try:
                if not killed:
                    self._journal.save_kill("external_kill")
                if not self._journal.is_paused():
                    self._journal.save_pause("external_kill")
            except Exception:
                self._paused = True
                try:
                    self._alert("execution_kill_persistence_failed")
                except Exception:
                    pass
            self._paused = True
            if not self._kill_alerted:
                try:
                    self._alert("execution_kill_latched")
                except Exception:
                    pass
                self._kill_alerted = True
            self._flatten_everything()
            return ControlMode.KILL
        if mode is ControlMode.PAUSE:
            if not self._external_pause_latched:
                self._pause("external_pause")
                self._external_pause_latched = True
            else:
                self._paused = True
            return ControlMode.PAUSE
        return ControlMode.RUN

    def _flatten_everything(self) -> bool:
        """Attempt every close/cancel, then verify no broker exposure remains."""
        complete = True
        try:
            positions = tuple(self._broker.get_positions())
        except Exception:
            positions = ()
            complete = False
        for position in positions:
            try:
                self._broker.close_position(position.deal_id)
            except Exception:
                complete = False
        try:
            working_orders = tuple(self._broker.get_open_orders())
        except Exception:
            working_orders = ()
            complete = False
        for order in working_orders:
            deal_id = self._working_order_id(order)
            if deal_id is None:
                complete = False
                continue
            try:
                self._broker.cancel_working_order(deal_id)
            except Exception:
                complete = False
        try:
            remaining_positions = tuple(self._broker.get_positions())
            remaining_orders = tuple(self._broker.get_open_orders())
            self._positions = remaining_positions
            complete = complete and not remaining_positions and not remaining_orders
        except Exception:
            complete = False
        if not complete:
            try:
                self._alert("execution_kill_flatten_incomplete")
            except Exception:
                pass
        return complete

    @staticmethod
    def _working_order_id(order: object) -> str | None:
        if not isinstance(order, Mapping):
            return None
        data = order.get("workingOrderData")
        if isinstance(data, Mapping):
            value = data.get("dealId")
        else:
            value = order.get("deal_id")
        return value.strip() if isinstance(value, str) and value.strip() else None

    def resolve_intent(
        self,
        cycle_id: str,
        outcome: str,
        *,
        deal_id: str | None = None,
        reason: str,
    ) -> None:
        """Operator control-plane recovery; never send or release a reservation.

        Without a broker deal reference, rejection requires an entirely empty
        position and activity result over the reservation interval. This is
        conservative: unrelated activity requires further operator investigation.
        No agent/LLM tool may expose this method.
        """
        with self._mutex:
            if not self._paused or not reason.strip():
                raise ExecutionPaused("resolution requires a pause and operator reason")
            try:
                self._execution_lease.check()
                self._guardian_lease.check()
                record = self._journal.get(cycle_id)
                if record is None or record["execution_state"] not in {"reserved", "ambiguous"}:
                    raise ValueError("intent is not unresolved")
                order = restored_order(record["execution_order"])
                positions = tuple(self._broker.get_positions())
                start = datetime.fromisoformat(record["reserved_at"])
                activity = self._broker.get_activity(start, datetime.now(UTC))
                if outcome == "matched":
                    matches = [p for p in positions if p.deal_id == deal_id]
                    if len(matches) != 1:
                        raise ValueError("broker position is absent or duplicated")
                    position = matches[0]
                    if (
                        position.epic != order.epic
                        or position.direction != order.direction
                        or position.size != order.size
                        or position.stop_level != order.stop_level
                    ):
                        raise ValueError("position does not match reserved intent")
                    state = "submitted"
                elif outcome == "rejected":
                    if deal_id is not None or positions or activity:
                        raise ValueError("broker evidence does not prove rejection")
                    state = "rejected"
                else:
                    raise ValueError("unsupported resolution outcome")
                self._journal.mark(cycle_id, state, deal_id=deal_id, resolution_reason=reason)
                self._alert("operator_intent_resolved")
            except Exception:
                self._pause("operator_intent_resolution_failed")
                raise ExecutionPaused("operator resolution failed") from None

    def submit(self, cycle_id: str, order: OrderRequest) -> OrderReceipt | None:
        with self._mutex:
            if not self._started:
                raise ExecutionPaused("execution has not started")
            mode = self._apply_controls()
            if mode is not ControlMode.RUN:
                raise ExecutionPaused("external pause or kill is active")
            if not self._started or self._paused:
                raise ExecutionPaused("entries are paused")
            try:
                self._execution_lease.check()
                if not self.reconcile():
                    raise ExecutionPaused("broker state mismatch")
                self._guardian_lease.check()
                approval = self._approve(order, cycle_id)
                decision = approval.decision
                if (
                    approval.order != order
                    or approval.cycle_id != cycle_id
                    or not decision.approved
                    or not decision.audit_written
                    or decision.halt_system
                    or decision.approved_size != order.size
                ):
                    raise ExecutionPaused("exact-order Guardian approval is required")
                if not self._journal.reserve(cycle_id, order):
                    return None
                self._execution_lease.check()
                self._guardian_lease.check()
            except Exception:
                self._pause("entry_preflight_failed")
                raise ExecutionPaused("entry preflight failed") from None
            mode = self._apply_controls()
            if mode is not ControlMode.RUN:
                try:
                    self._journal.mark(cycle_id, "rejected")
                except Exception:
                    self._pause("control_blocked_order_persistence_failed")
                raise ExecutionPaused("control activated before broker submission")
            try:
                receipt = self._broker.place_order(order)
                if (
                    receipt.client_order_id != order.client_order_id
                    or receipt.deal_id != receipt.position.deal_id
                    or receipt.position.epic != order.epic
                    or receipt.position.direction != order.direction
                    or receipt.position.size != order.size
                    or receipt.position.stop_level != order.stop_level
                ):
                    raise ValueError("broker receipt mismatch")
                self._journal.mark(cycle_id, "submitted", deal_id=receipt.deal_id)
                self.reconcile()
            except Exception:
                self._pause("order_outcome_ambiguous")
                try:
                    self._journal.mark(cycle_id, "ambiguous")
                except Exception:
                    pass
                try:
                    self.reconcile()
                except Exception:
                    pass
                raise ExecutionPaused(
                    "order outcome requires reconciliation; do not retry"
                ) from None
            mode = self._apply_controls()
            if mode is ControlMode.KILL:
                raise ExecutionPaused("kill activated during order submission")
            return receipt
