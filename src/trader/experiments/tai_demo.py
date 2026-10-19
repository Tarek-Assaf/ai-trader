"""Owner-directed Tai demo experiment, fail-closed and mockable.

This module is deliberately a small adapter around the existing execution
boundary.  It computes no position size and never calls a broker to submit an
entry: the injected ``OrderManager`` owns Guardian approval and reservation.
The first real demo run must still be enabled by the owner after deployment.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from trader.execution.brokers.base import Direction, OrderRequest, Position
from trader.execution.control import ControlMode
from trader.execution.manager import ExecutionPaused

LOGGER = logging.getLogger(__name__)
EXPERIMENT_LABEL = "Tai port, owner-directed demo experiment, loosely matched to the owner"


class TaiDemoRefused(RuntimeError):
    """Raised when the demo experiment is not explicitly enabled."""


@dataclass(frozen=True, slots=True)
class TaiDemoSettings:
    """The three independent startup gates for the owner-directed experiment."""

    broker_env: str
    trading_mode: str
    live_trading_enabled: bool
    experiment_enabled: bool

    @property
    def enabled(self) -> bool:
        return (
            self.broker_env == "demo"
            and self.trading_mode == "paper"
            and not self.live_trading_enabled
            and self.experiment_enabled
        )

    @classmethod
    def from_settings(cls, settings: object) -> TaiDemoSettings:
        return cls(
            broker_env=str(getattr(settings, "broker_env", "")),
            trading_mode=str(getattr(settings, "trading_mode", "")),
            live_trading_enabled=bool(getattr(settings, "live_trading_enabled", True)),
            experiment_enabled=bool(getattr(settings, "tai_demo_experiment", False)),
        )

    def require_enabled(self) -> None:
        if not self.enabled:
            raise TaiDemoRefused(
                "TAI_DEMO_EXPERIMENT requires BROKER_ENV=demo, TRADING_MODE=paper "
                "and LIVE_TRADING_ENABLED=false"
            )


@dataclass(frozen=True, slots=True)
class ClosedBar:
    """A closed 3-minute recorder bar delivered by the runtime feed."""

    at: datetime
    epic: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    timeframe_minutes: int = 3
    closed: bool = True
    market_open: bool = True
    in_break: bool = False

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("bar timestamp must be timezone-aware")
        if self.timeframe_minutes != 3:
            raise ValueError("Tai demo accepts only 3-minute bars")
        if not self.epic.strip() or any(
            not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            for value in (self.open, self.high, self.low, self.close)
        ):
            raise ValueError("bar prices must be positive finite decimals")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("bar high/low do not contain open/close")


@dataclass(frozen=True, slots=True)
class TaiSignal:
    """The unchanged T-162 signal and structure stop, without a position size."""

    direction: Direction
    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.direction, Direction):
            raise ValueError("direction must be BUY or SELL")
        for name, value in (("entry", self.entry), ("stop", self.stop)):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be a positive finite Decimal")
        if self.direction is Direction.BUY and self.stop >= self.entry:
            raise ValueError("BUY structure stop must be below entry")
        if self.direction is Direction.SELL and self.stop <= self.entry:
            raise ValueError("SELL structure stop must be above entry")
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            for value in self.targets
        ):
            raise ValueError("targets must be positive finite decimals")
        if len(self.targets) > 3:
            raise ValueError("Tai demo supports at most three scale-out targets")

    def levels(self) -> tuple[Decimal, Decimal, Decimal]:
        """Return the 1R/2R/3R levels, filling absent levels deterministically."""
        risk = abs(self.entry - self.stop)
        defaults = (
            self.entry + risk if self.direction is Direction.BUY else self.entry - risk,
            self.entry + risk * 2 if self.direction is Direction.BUY else self.entry - risk * 2,
            self.entry + risk * 3 if self.direction is Direction.BUY else self.entry - risk * 3,
        )
        levels = list(defaults)
        levels[: len(self.targets)] = self.targets
        for previous, current in zip(levels, levels[1:]):
            if self.direction is Direction.BUY and current <= previous:
                raise ValueError("BUY targets must increase")
            if self.direction is Direction.SELL and current >= previous:
                raise ValueError("SELL targets must decrease")
        return tuple(levels)


@dataclass(frozen=True, slots=True)
class DemoOutcome:
    """Secret-free result of one bar decision."""

    action: str
    reason: str
    cycle_id: str
    label: str = EXPERIMENT_LABEL


class Control(Protocol):
    def read(self) -> ControlMode: ...


class PositionGateway(Protocol):
    def get_positions(self) -> Sequence[Position]: ...

    def modify_position(
        self, deal_id: str, *, stop_level: Decimal, profit_level: Decimal | None = None
    ) -> str: ...

    def close_position(self, deal_id: str) -> str: ...


class OrderManager(Protocol):
    def submit(self, cycle_id: str, order: OrderRequest) -> object | None: ...


SignalProvider = Callable[[ClosedBar], TaiSignal | None]
OrderFactory = Callable[[TaiSignal, str], OrderRequest]
Journal = Callable[[Mapping[str, object]], None]
Alert = Callable[[str], None]


@dataclass(slots=True)
class _ManagedPosition:
    entry: Decimal
    risk: Decimal
    direction: Direction
    targets: tuple[Decimal, Decimal, Decimal]
    original_size: Decimal
    hit: set[int] = field(default_factory=set)
    entry_stop_moved: bool = False


class TaiDemoRunner:
    """Run Tai signals through OrderManager and manage mockable exits.

    The runner owns no sizing, Guardian logic or broker client.  ``order_factory``
    is supplied by the composition layer after its normal risk sizing context is
    assembled.  This keeps the strategy unable to bypass the Risk Guardian.
    """

    def __init__(
        self,
        *,
        settings: TaiDemoSettings,
        order_manager: OrderManager,
        position_gateway: PositionGateway,
        signal_provider: SignalProvider,
        order_factory: OrderFactory,
        control: Control,
        journal: Journal | None = None,
        alert: Alert | None = None,
    ) -> None:
        settings.require_enabled()
        self.settings = settings
        self._orders = order_manager
        self._positions = position_gateway
        self._signal = signal_provider
        self._order_factory = order_factory
        self._control = control
        self._journal = journal or (lambda _event: None)
        self._alert = alert or (lambda message: LOGGER.info("tai demo: %s", message))
        self._managed: dict[str, _ManagedPosition] = {}

    def on_bar(self, bar: ClosedBar) -> DemoOutcome:
        """Process exactly one closed 3m bar; never trade an incomplete bar."""

        cycle_id = f"tai-demo-{bar.at.astimezone(UTC).strftime('%Y%m%dT%H%M%S')}-{bar.epic}"
        if not bar.closed:
            return self._outcome(cycle_id, "NO_TRADE", "bar_not_closed")
        mode = self._control.read()
        if mode is ControlMode.KILL:
            self._flatten(cycle_id)
            return self._outcome(cycle_id, "NO_TRADE", "kill_switch_active")
        if mode is not ControlMode.RUN:
            return self._outcome(cycle_id, "NO_TRADE", "execution_paused")
        self._manage_exits(bar, cycle_id)
        if not bar.market_open or bar.in_break:
            return self._outcome(cycle_id, "NO_TRADE", "market_closed_or_blackout")

        positions = tuple(
            position for position in self._positions.get_positions() if position.epic == bar.epic
        )
        if positions:
            self._journal_event(cycle_id, "position_open", epic=bar.epic)
            return self._outcome(cycle_id, "NO_TRADE", "one_position_rule")
        signal = self._signal(bar)
        if signal is None:
            return self._outcome(cycle_id, "NO_TRADE", "no_signal")
        order = self._order_factory(signal, cycle_id)
        if (
            order.client_order_id != cycle_id
            or order.epic != bar.epic
            or order.reference_price != signal.entry
            or order.stop_level != signal.stop
            or order.profit_level != signal.levels()[2]
        ):
            self._journal_event(cycle_id, "order_refused", reason="order_factory_mismatch")
            return self._outcome(cycle_id, "REJECTED", "order_factory_mismatch")
        try:
            receipt = self._orders.submit(cycle_id, order)
        except ExecutionPaused:
            self._journal_event(cycle_id, "guardian_veto", epic=bar.epic)
            self._alert(f"{EXPERIMENT_LABEL}: entry vetoed for {bar.epic}")
            return self._outcome(cycle_id, "REJECTED", "guardian_veto")
        if receipt is None:
            self._journal_event(cycle_id, "guardian_veto", epic=bar.epic)
            return self._outcome(cycle_id, "REJECTED", "guardian_veto")
        position = getattr(receipt, "position", None)
        if not isinstance(position, Position):
            self._journal_event(cycle_id, "order_refused", reason="receipt_missing_position")
            return self._outcome(cycle_id, "REJECTED", "receipt_missing_position")
        risk = abs(signal.entry - signal.stop)
        self._managed[position.deal_id] = _ManagedPosition(
            entry=signal.entry,
            risk=risk,
            direction=signal.direction,
            targets=signal.levels(),
            original_size=position.size,
        )
        self._journal_event(cycle_id, "entry", epic=bar.epic, deal_id=position.deal_id)
        self._alert(f"{EXPERIMENT_LABEL}: entry {signal.direction.value} {bar.epic}")
        return self._outcome(cycle_id, "SUBMITTED", "entry_submitted")

    def _manage_exits(self, bar: ClosedBar, cycle_id: str) -> None:
        for position in tuple(self._positions.get_positions()):
            if position.epic != bar.epic:
                continue
            state = self._managed.get(position.deal_id)
            if state is None:
                continue
            for index, target in enumerate(state.targets, start=1):
                if index in state.hit or not self._target_hit(state.direction, bar, target):
                    continue
                state.hit.add(index)
                if index == 1 and not state.entry_stop_moved:
                    self._positions.modify_position(
                        position.deal_id,
                        stop_level=state.entry,
                        profit_level=state.targets[2],
                    )
                    state.entry_stop_moved = True
                    self._journal_event(cycle_id, "stop_to_entry", deal_id=position.deal_id)
                if index == 3:
                    self._positions.close_position(position.deal_id)
                    self._managed.pop(position.deal_id, None)
                    self._journal_event(
                        cycle_id, "exit", deal_id=position.deal_id, reason="target_3"
                    )
                    self._alert(f"{EXPERIMENT_LABEL}: exit {bar.epic} target_3")
                    break
                close_partial = getattr(self._positions, "close_partial", None)
                if callable(close_partial):
                    current_size = position.size
                    amount = min(current_size, state.original_size / Decimal("3"))
                    if amount > 0 and amount < current_size:
                        close_partial(position.deal_id, amount)
                        self._journal_event(
                            cycle_id, "scale_out", deal_id=position.deal_id, target=index
                        )
                    else:
                        self._journal_event(
                            cycle_id,
                            "scale_out_unavailable",
                            deal_id=position.deal_id,
                            target=index,
                        )

    def _flatten(self, cycle_id: str) -> None:
        for position in tuple(self._positions.get_positions()):
            try:
                self._positions.close_position(position.deal_id)
            finally:
                self._managed.pop(position.deal_id, None)
        self._journal_event(cycle_id, "kill_switch", reason="flatten_requested")
        self._alert(f"{EXPERIMENT_LABEL}: kill switch flattened positions")

    @staticmethod
    def _target_hit(direction: Direction, bar: ClosedBar, target: Decimal) -> bool:
        return bar.high >= target if direction is Direction.BUY else bar.low <= target

    def _journal_event(self, cycle_id: str, event: str, **details: object) -> None:
        payload: dict[str, object] = {
            "cycle_id": cycle_id,
            "event": event,
            "label": EXPERIMENT_LABEL,
            **details,
        }
        self._journal(payload)

    @staticmethod
    def _outcome(cycle_id: str, action: str, reason: str) -> DemoOutcome:
        return DemoOutcome(action, reason, cycle_id)


__all__ = [
    "ClosedBar",
    "DemoOutcome",
    "EXPERIMENT_LABEL",
    "TaiDemoRefused",
    "TaiDemoRunner",
    "TaiDemoSettings",
    "TaiSignal",
]
