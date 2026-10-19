"""Owner-directed Tai demo experiment, fail-closed and mockable.

This module is deliberately a small adapter around the existing execution
boundary.  It computes no position size and never calls a broker to submit an
entry: the injected ``OrderManager`` owns Guardian approval and reservation.
The first real demo run must still be enabled by the owner after deployment.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

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
    # Kept immediately after the four startup gates so the existing positional
    # test fixtures can opt into/out of dry-run without shifting sizing fields.
    dry_run: bool = False
    base_lot: Decimal = Decimal("0.1")
    start_balance: Decimal = Decimal("10000")

    @property
    def enabled(self) -> bool:
        return (
            self.broker_env == "demo"
            and self.trading_mode == "paper"
            and not self.live_trading_enabled
            and self.experiment_enabled
        )

    def __post_init__(self) -> None:
        for name in ("base_lot", "start_balance"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be a positive finite Decimal")

    @classmethod
    def from_settings(cls, settings: object) -> TaiDemoSettings:
        start = Decimal(str(getattr(settings, "tai_demo_start_balance", "0")))
        if getattr(settings, "tai_demo_experiment", False) and start <= 0:
            raise TaiDemoRefused(
                "TAI_DEMO_START_BALANCE must be set to the demo balance at the start of the "
                "experiment (lot = 0.1 x balance / start balance)"
            )
        return cls(
            broker_env=str(getattr(settings, "broker_env", "")),
            trading_mode=str(getattr(settings, "trading_mode", "")),
            live_trading_enabled=bool(getattr(settings, "live_trading_enabled", True)),
            experiment_enabled=bool(getattr(settings, "tai_demo_experiment", False)),
            base_lot=Decimal(str(getattr(settings, "tai_demo_base_lot", "0.1"))),
            # The disabled default does not need a balance.  Keep the value
            # valid for the immutable settings object; the enabled path above
            # still refuses an unset owner-provided start balance.
            start_balance=start if start > 0 else Decimal("1"),
            dry_run=bool(getattr(settings, "tai_demo_dry_run", True)),
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
    volume: Decimal = Decimal("0")

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
        if not isinstance(self.volume, Decimal) or not self.volume.is_finite() or self.volume < 0:
            raise ValueError("bar volume must be a finite non-negative Decimal")


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
        for previous, current in zip(levels, levels[1:], strict=False):
            if self.direction is Direction.BUY and current <= previous:
                raise ValueError("BUY targets must increase")
            if self.direction is Direction.SELL and current >= previous:
                raise ValueError("SELL targets must decrease")
        return (levels[0], levels[1], levels[2])


@dataclass(frozen=True, slots=True)
class TaiInstrumentRules:
    """Broker sizing rules read from the instrument metadata before each entry."""

    min_size: Decimal
    size_step: Decimal
    value_per_point: Decimal | None = None
    tick_size: Decimal | None = None
    min_stop_distance: Decimal | None = None
    min_limit_distance: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("min_size", "size_step"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be a positive finite Decimal")
        if self.value_per_point is not None and (
            not isinstance(self.value_per_point, Decimal)
            or not self.value_per_point.is_finite()
            or self.value_per_point <= 0
        ):
            raise ValueError("value_per_point must be a positive finite Decimal")
        for name in ("tick_size", "min_stop_distance", "min_limit_distance"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite Decimal")


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
OrderFactory = Callable[..., OrderRequest]
BalanceProvider = Callable[[], Decimal]
InstrumentRulesProvider = Callable[[str], TaiInstrumentRules]
Journal = Callable[[Mapping[str, object]], None]
Alert = Callable[[str], None]


@dataclass(slots=True)
class _ManagedPosition:
    epic: str
    entry: Decimal
    risk: Decimal
    direction: Direction
    targets: tuple[Decimal, Decimal, Decimal]
    original_size: Decimal
    size_step: Decimal | None = None
    one_r_value: Decimal | None = None
    hit: set[int] = field(default_factory=set)
    entry_stop_moved: bool = False
    shadow: bool = False
    remaining_size: Decimal | None = None


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
        balance_provider: BalanceProvider | None = None,
        instrument_rules_provider: InstrumentRulesProvider | None = None,
        dry_run: bool | None = None,
        journal: Journal | None = None,
        alert: Alert | None = None,
        managed_state: Mapping[str, _ManagedPosition] | None = None,
        state_path: Path | None = None,
        epic: str | None = None,
    ) -> None:
        settings.require_enabled()
        self.settings = settings
        self._orders = order_manager
        self._positions = position_gateway
        self._signal = signal_provider
        self._order_factory = order_factory
        self._control = control
        self._balance_provider = balance_provider
        self._rules_provider = instrument_rules_provider
        self._dry_run = settings.dry_run if dry_run is None else dry_run
        self._journal = journal or (lambda _event: None)
        self._alert = alert or (lambda message: LOGGER.info("tai demo: %s", message))
        self._state_path = state_path
        self._epic = epic
        self._startup_blocked = False
        self._state_corrupt = False
        self._refusal_alerted: set[str] = set()
        persisted_state = self._load_state()
        # Runtime composition may be retried after a dependency outage. Keep
        # Tai position bookkeeping across that retry so an open demo position
        # remains managed and cannot trigger a duplicate entry.
        self._managed = {
            deal_id: replace(state, hit=set(state.hit))
            for deal_id, state in {**persisted_state, **(managed_state or {})}.items()
        }
        if self._epic is None and self._managed:
            self._epic = next(iter(self._managed.values())).epic
        self._reconcile_startup()

    def snapshot_state(self) -> dict[str, _ManagedPosition]:
        """Return detached state for an in-process runtime composition retry."""
        return {
            deal_id: replace(state, hit=set(state.hit)) for deal_id, state in self._managed.items()
        }

    def _load_state(self) -> dict[str, _ManagedPosition]:
        if self._state_path is None or not self._state_path.exists():
            return {}
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            positions = payload.get("positions")
            if not isinstance(positions, dict):
                raise ValueError("positions must be an object")
            loaded: dict[str, _ManagedPosition] = {}
            for deal_id, raw in positions.items():
                if not isinstance(raw, dict) or not isinstance(deal_id, str) or not deal_id:
                    raise ValueError("invalid Tai position record")
                direction = Direction(str(raw["direction"]))
                targets = tuple(Decimal(str(value)) for value in raw["targets"])
                if len(targets) != 3:
                    raise ValueError("Tai position targets must contain three levels")
                loaded[deal_id] = _ManagedPosition(
                    epic=str(raw["epic"]),
                    entry=Decimal(str(raw["entry"])),
                    risk=Decimal(str(raw["risk"])),
                    direction=direction,
                    targets=(targets[0], targets[1], targets[2]),
                    original_size=Decimal(str(raw["original_size"])),
                    size_step=(
                        Decimal(str(raw["size_step"])) if raw.get("size_step") is not None else None
                    ),
                    one_r_value=(
                        Decimal(str(raw["one_r_value"]))
                        if raw.get("one_r_value") is not None
                        else None
                    ),
                    hit={int(index) for index in raw.get("hit", [])},
                    entry_stop_moved=bool(raw.get("entry_stop_moved", False)),
                    shadow=bool(raw.get("shadow", False)),
                    remaining_size=(
                        Decimal(str(raw["remaining_size"]))
                        if raw.get("remaining_size") is not None
                        else None
                    ),
                )
            return loaded
        except (OSError, TypeError, ValueError, KeyError) as exc:
            self._startup_blocked = True
            self._state_corrupt = True
            self._journal_event("tai-startup", "state_corrupt", reason=type(exc).__name__)
            self._send_alert("Tai demo state is corrupt; entries are refused.")
            return {}

    def _reconcile_startup(self) -> None:
        if self._dry_run:
            self._persist_state()
            return
        try:
            positions = tuple(self._positions.get_positions())
        except Exception as exc:  # noqa: BLE001 - startup must fail closed
            self._startup_blocked = True
            self._journal_event("tai-startup", "reconcile_failed", reason=type(exc).__name__)
            self._send_alert("Tai demo startup reconciliation failed; entries are refused.")
            return
        by_id = {position.deal_id: position for position in positions}
        for deal_id, state in tuple(self._managed.items()):
            if state.shadow:
                self._managed.pop(deal_id, None)
                continue
            position = by_id.get(deal_id)
            if position is None or position.epic != state.epic:
                self._managed.pop(deal_id, None)
                self._journal_event("tai-startup", "reconcile_closed", deal_id=deal_id)
        for position in positions:
            if (
                self._epic is not None
                and position.epic == self._epic
                and position.deal_id not in self._managed
            ):
                self._startup_blocked = True
                self._journal_event(
                    "tai-startup",
                    "reconcile_unmanaged",
                    deal_id=position.deal_id,
                    epic=position.epic,
                )
                self._send_alert(
                    f"Tai demo found an unmanaged {position.epic} position; entries are refused."
                )
        self._persist_state()

    def _persist_state(self) -> None:
        if self._state_path is None or self._state_corrupt:
            return
        payload = {
            "version": 1,
            "positions": {
                deal_id: {
                    "epic": state.epic,
                    "direction": state.direction.value,
                    "entry": str(state.entry),
                    "risk": str(state.risk),
                    "targets": [str(value) for value in state.targets],
                    "original_size": str(state.original_size),
                    "hit": sorted(state.hit),
                    "size_step": str(state.size_step) if state.size_step is not None else None,
                    "one_r_value": (
                        str(state.one_r_value) if state.one_r_value is not None else None
                    ),
                    "entry_stop_moved": state.entry_stop_moved,
                    "shadow": state.shadow,
                    "remaining_size": (
                        str(state.remaining_size) if state.remaining_size is not None else None
                    ),
                }
                for deal_id, state in self._managed.items()
            },
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix="tai-demo-", suffix=".json", dir=self._state_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._state_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def on_bar(self, bar: ClosedBar) -> DemoOutcome:
        """Process exactly one closed 3m bar; never trade an incomplete bar."""

        cycle_id = f"tai-demo-{bar.at.astimezone(UTC).strftime('%Y%m%dT%H%M%S')}-{bar.epic}"
        if not bar.closed:
            return self._refusal(cycle_id, "bar_not_closed", event="bar_not_closed")
        mode = self._control.read()
        if mode is ControlMode.KILL:
            self._flatten(cycle_id)
            return self._outcome(cycle_id, "NO_TRADE", "kill_switch_active")
        if mode is not ControlMode.RUN:
            return self._refusal(cycle_id, "execution_paused", event="execution_paused")
        self._manage_exits(bar, cycle_id)
        if not bar.market_open or bar.in_break:
            return self._refusal(
                cycle_id,
                "market_closed_or_blackout",
                event="market_closed_or_blackout",
            )

        positions = (
            tuple(
                position
                for position in self._positions.get_positions()
                if position.epic == bar.epic
            )
            if not self._dry_run
            else ()
        )
        if positions or (
            self._dry_run and any(state.epic == bar.epic for state in self._managed.values())
        ):
            return self._refusal(
                cycle_id,
                "one_position_rule",
                event="position_open",
                epic=bar.epic,
            )
        if self._startup_blocked:
            return self._refusal(
                cycle_id,
                "startup_reconciliation_failed",
                event="startup_reconciliation_failed",
                action="REJECTED",
            )
        signal = self._signal(bar)
        if signal is None:
            self._journal_event(cycle_id, "no_signal", reason="no_signal")
            return self._outcome(cycle_id, "NO_TRADE", "no_signal")
        try:
            rules = self._rules_provider(bar.epic) if self._rules_provider is not None else None
            requested_size = self._requested_size(rules)
            order = self._build_order(signal, cycle_id, requested_size)
        except (TaiDemoRefused, ValueError) as exc:
            return self._refusal(
                cycle_id,
                "order_rules_unavailable",
                event="order_refused",
                action="REJECTED",
                epic=bar.epic,
                detail=str(exc),
            )
        if requested_size is not None and order.size != requested_size:
            order = replace(order, size=requested_size)
        allows_price_rounding = bool(getattr(self._order_factory, "allows_price_rounding", False))
        if (
            order.client_order_id != cycle_id
            or order.epic != bar.epic
            or order.reference_price != signal.entry
            or (
                not allows_price_rounding
                and (order.stop_level != signal.stop or order.profit_level != signal.levels()[2])
            )
        ):
            return self._refusal(
                cycle_id,
                "order_factory_mismatch",
                event="order_refused",
                action="REJECTED",
            )
        if self._dry_run:
            shadow_id = f"shadow:{cycle_id}"
            self._managed[shadow_id] = _ManagedPosition(
                epic=bar.epic,
                entry=signal.entry,
                risk=abs(signal.entry - signal.stop),
                direction=signal.direction,
                targets=signal.levels(),
                original_size=order.size,
                size_step=rules.size_step if rules is not None else None,
                one_r_value=(
                    abs(signal.entry - signal.stop) * order.size * rules.value_per_point
                    if rules is not None and rules.value_per_point is not None
                    else None
                ),
                shadow=True,
                remaining_size=order.size,
            )
            self._persist_state()
            self._journal_event(
                cycle_id,
                "dry_run_would_submit",
                epic=bar.epic,
                direction=order.direction.value,
                size=order.size,
                stop_level=order.stop_level,
                profit_level=order.profit_level,
            )
            self._send_alert(f"entry {signal.direction.value} {bar.epic}")
            return self._outcome(cycle_id, "DRY_RUN", "order_would_submit")
        try:
            receipt = self._orders.submit(cycle_id, order)
        except ExecutionPaused:
            return self._refusal(
                cycle_id,
                "guardian_veto",
                event="guardian_veto",
                action="REJECTED",
                epic=bar.epic,
            )
        if receipt is None:
            return self._refusal(
                cycle_id,
                "receipt_none",
                event="guardian_veto",
                action="REJECTED",
                epic=bar.epic,
            )
        position = getattr(receipt, "position", None)
        if not isinstance(position, Position):
            return self._refusal(
                cycle_id,
                "receipt_missing_position",
                event="order_refused",
                action="REJECTED",
            )
        risk = abs(signal.entry - signal.stop)
        self._managed[position.deal_id] = _ManagedPosition(
            epic=bar.epic,
            entry=signal.entry,
            risk=risk,
            direction=signal.direction,
            targets=signal.levels(),
            original_size=position.size,
            size_step=rules.size_step if rules is not None else None,
            one_r_value=(
                abs(signal.entry - signal.stop) * position.size * rules.value_per_point
                if rules is not None and rules.value_per_point is not None
                else None
            ),
            remaining_size=position.size,
        )
        self._journal_event(
            cycle_id,
            "entry",
            epic=bar.epic,
            deal_id=position.deal_id,
            one_r_value=self._managed[position.deal_id].one_r_value,
        )
        self._persist_state()
        self._send_alert(f"entry {signal.direction.value} {bar.epic}")
        return self._outcome(cycle_id, "SUBMITTED", "entry_submitted")

    def _manage_exits(self, bar: ClosedBar, cycle_id: str) -> None:
        if self._dry_run:
            self._manage_shadow_exits(bar, cycle_id)
            return
        try:
            positions = tuple(self._positions.get_positions())
        except Exception as exc:  # noqa: BLE001 - exit management must fail safe
            self._exit_failure(cycle_id, "positions", None, None, exc)
            return
        present_ids = {position.deal_id for position in positions}
        for deal_id, tracked in tuple(self._managed.items()):
            if tracked.shadow or tracked.epic != bar.epic or deal_id in present_ids:
                continue
            self._managed.pop(deal_id, None)
            self._journal_event(
                cycle_id, "reconcile_closed", deal_id=deal_id, reason="broker_closed"
            )
            self._send_alert(f"flat {bar.epic} broker_closed")
            self._persist_state()
        for position in positions:
            if position.epic != bar.epic:
                continue
            state = self._managed.get(position.deal_id)
            if state is None:
                continue
            current_size = position.size
            for index, target in enumerate(state.targets, start=1):
                if index in state.hit or not self._target_hit(state.direction, bar, target):
                    continue
                if index == 1 and not state.entry_stop_moved:
                    try:
                        self._positions.modify_position(
                            position.deal_id,
                            stop_level=state.entry,
                            profit_level=state.targets[2],
                        )
                    except Exception as exc:  # noqa: BLE001 - preserve the position
                        self._exit_failure(cycle_id, "modify", position.deal_id, index, exc)
                        state.hit.discard(index)
                        self._persist_state()
                        return
                    state.entry_stop_moved = True
                    self._journal_event(cycle_id, "stop_to_entry", deal_id=position.deal_id)
                    self._send_alert(f"stop moved to entry for {bar.epic}")
                if index == 3:
                    try:
                        result = self._positions.close_position(position.deal_id)
                    except Exception as exc:  # noqa: BLE001 - preserve the position
                        self._exit_failure(cycle_id, "close", position.deal_id, index, exc)
                        state.hit.discard(index)
                        self._persist_state()
                        return
                    if self._is_partial_fill(result):
                        filled = self._filled_size(result)
                        if filled is not None and Decimal("0") < filled < position.size:
                            state.remaining_size = position.size - filled
                        self._partial_exit_failure(
                            cycle_id, position.deal_id, index, state, result, position.size
                        )
                        state.hit.discard(index)
                        self._persist_state()
                        return
                    state.hit.add(index)
                    self._managed.pop(position.deal_id, None)
                    self._journal_event(
                        cycle_id, "exit", deal_id=position.deal_id, reason="target_3"
                    )
                    self._send_alert(f"exit {bar.epic} target_3")
                    self._persist_state()
                    break
                close_partial = getattr(self._positions, "close_partial", None)
                if callable(close_partial):
                    amount = self._partial_amount(state, current_size, index)
                    if amount > 0 and amount < current_size:
                        try:
                            result = close_partial(position.deal_id, amount)
                        except Exception as exc:  # noqa: BLE001 - preserve the position
                            self._exit_failure(
                                cycle_id, "partial_close", position.deal_id, index, exc
                            )
                            state.hit.discard(index)
                            self._persist_state()
                            return
                        if self._is_partial_fill(result):
                            filled = self._filled_size(result)
                            if filled is not None and Decimal("0") < filled < amount:
                                current_size -= filled
                                state.remaining_size = current_size
                            self._partial_exit_failure(
                                cycle_id, position.deal_id, index, state, result, amount
                            )
                            state.hit.discard(index)
                            self._persist_state()
                            return
                        current_size -= amount
                        state.remaining_size = current_size
                        state.hit.add(index)
                        self._journal_event(
                            cycle_id,
                            "scale_out",
                            deal_id=position.deal_id,
                            target=index,
                            amount=amount,
                            fraction=str(self._exit_fraction(index)),
                        )
                        self._send_alert(f"partial exit {bar.epic} target_{index}")
                    else:
                        state.hit.add(index)
                        self._journal_event(
                            cycle_id,
                            "scale_out_unavailable",
                            deal_id=position.deal_id,
                            target=index,
                        )
                else:
                    state.hit.add(index)
                self._persist_state()

    def _exit_failure(
        self,
        cycle_id: str,
        operation: str,
        deal_id: str | None,
        index: int | None,
        exc: BaseException,
    ) -> None:
        """Record one bounded exit failure without exposing broker details."""

        self._journal_event(
            cycle_id,
            "exit_error",
            operation=operation,
            deal_id=deal_id,
            target=index,
            error_type=type(exc).__name__,
        )
        key = f"exit:{operation}:{deal_id or 'unknown'}:{index or 0}"
        if key not in self._refusal_alerted:
            self._refusal_alerted.add(key)
            self._send_alert(f"Tai {operation} failed; position remains managed for retry.")

    def _partial_exit_failure(
        self,
        cycle_id: str,
        deal_id: str,
        index: int,
        state: _ManagedPosition,
        result: object,
        requested: Decimal,
    ) -> None:
        """Record an uncertain or partial close while keeping state persisted."""

        self._journal_event(
            cycle_id,
            "exit_partial_fill",
            operation="close" if index == 3 else "partial_close",
            deal_id=deal_id,
            target=index,
            requested=requested,
            status=self._result_status(result),
            remaining_size=state.remaining_size,
        )
        key = f"exit:partial:{deal_id}:{index}"
        if key not in self._refusal_alerted:
            self._refusal_alerted.add(key)
            self._send_alert("Tai exit partially filled; position remains managed for retry.")

    @staticmethod
    def _result_status(result: object) -> str | None:
        if isinstance(result, Mapping):
            for key in ("status", "dealStatus", "deal_status"):
                value = result.get(key)
                if value is not None:
                    return str(value)
        value = getattr(result, "status", None)
        return str(value) if value is not None else None

    @classmethod
    def _is_partial_fill(cls, result: object) -> bool:
        status = cls._result_status(result)
        if status is None:
            return isinstance(result, str) and "PARTIAL_FILL" in result.upper()
        return "PARTIAL" in status.upper()

    @staticmethod
    def _filled_size(result: object) -> Decimal | None:
        values: Mapping[str, object] | None = result if isinstance(result, Mapping) else None
        raw = None
        if values is not None:
            for key in ("filled_size", "filledSize", "filled", "size"):
                if key in values:
                    raw = values[key]
                    break
        if raw is None:
            raw = getattr(result, "filled_size", None)
        try:
            amount = Decimal(str(raw)) if raw is not None else None
        except (InvalidOperation, TypeError, ValueError):
            return None
        return amount if amount is not None and amount.is_finite() and amount > 0 else None

    def _manage_shadow_exits(self, bar: ClosedBar, cycle_id: str) -> None:
        for deal_id, state in tuple(self._managed.items()):
            if not state.shadow or state.epic != bar.epic:
                continue
            current_size = state.remaining_size or state.original_size
            for index, target in enumerate(state.targets, start=1):
                if index in state.hit or not self._target_hit(state.direction, bar, target):
                    continue
                state.hit.add(index)
                if index == 1 and not state.entry_stop_moved:
                    state.entry_stop_moved = True
                    self._journal_event(cycle_id, "stop_to_entry", deal_id=deal_id)
                    self._send_alert(f"stop moved to entry for {bar.epic}")
                if index == 3:
                    self._managed.pop(deal_id, None)
                    self._journal_event(cycle_id, "exit", deal_id=deal_id, reason="target_3")
                    self._send_alert(f"exit {bar.epic} target_3")
                    self._persist_state()
                    break
                amount = self._partial_amount(state, current_size, index)
                if amount > 0 and amount < current_size:
                    current_size -= amount
                    state.remaining_size = current_size
                    self._journal_event(
                        cycle_id,
                        "scale_out",
                        deal_id=deal_id,
                        target=index,
                        amount=amount,
                        fraction=str(self._exit_fraction(index)),
                    )
                    self._send_alert(f"partial exit {bar.epic} target_{index}")
                else:
                    self._journal_event(
                        cycle_id, "scale_out_unavailable", deal_id=deal_id, target=index
                    )
                self._persist_state()

            if deal_id not in self._managed:
                continue
            stop_level = (
                state.entry
                if state.entry_stop_moved
                else (
                    state.entry - state.risk
                    if state.direction is Direction.BUY
                    else state.entry + state.risk
                )
            )
            if not self._stop_hit(state.direction, bar, stop_level):
                continue
            reason = "stop_entry" if state.entry_stop_moved else "stop"
            self._managed.pop(deal_id, None)
            self._journal_event(cycle_id, "exit", deal_id=deal_id, reason=reason)
            self._send_alert(f"flat {bar.epic} {reason}")
            self._persist_state()

    def _flatten(self, cycle_id: str) -> None:
        if self._dry_run:
            for deal_id, state in tuple(self._managed.items()):
                if state.shadow:
                    self._managed.pop(deal_id, None)
            self._persist_state()
            self._journal_event(cycle_id, "kill_switch", reason="flatten_requested")
            self._send_alert("kill switch flattened positions")
            return
        for position in tuple(self._positions.get_positions()):
            try:
                self._positions.close_position(position.deal_id)
            finally:
                self._managed.pop(position.deal_id, None)
        self._persist_state()
        self._journal_event(cycle_id, "kill_switch", reason="flatten_requested")
        self._send_alert("kill switch flattened positions")

    @staticmethod
    def _target_hit(direction: Direction, bar: ClosedBar, target: Decimal) -> bool:
        return bar.high >= target if direction is Direction.BUY else bar.low <= target

    @staticmethod
    def _stop_hit(direction: Direction, bar: ClosedBar, stop: Decimal) -> bool:
        return bar.low <= stop if direction is Direction.BUY else bar.high >= stop

    def _requested_size(self, rules: TaiInstrumentRules | None) -> Decimal | None:
        if rules is None:
            return None
        if self._balance_provider is None:
            raise TaiDemoRefused("demo balance provider is required")
        balance = self._balance_provider()
        if not isinstance(balance, Decimal) or not balance.is_finite() or balance <= 0:
            raise TaiDemoRefused("demo balance must be a positive finite Decimal")
        raw = max(
            self.settings.base_lot,
            self.settings.base_lot * balance / self.settings.start_balance,
        )
        aligned = (raw / rules.size_step).to_integral_value(rounding=ROUND_DOWN) * rules.size_step
        if aligned < rules.min_size:
            raise TaiDemoRefused("requested demo size is below the broker minimum size")
        return aligned

    def _build_order(
        self, signal: TaiSignal, cycle_id: str, requested_size: Decimal | None
    ) -> OrderRequest:
        """Support both the original two-argument factory and size-aware factories."""
        try:
            parameters = inspect.signature(self._order_factory).parameters.values()
            positional = [
                parameter
                for parameter in parameters
                if parameter.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
        except (TypeError, ValueError):
            positional = []
        if requested_size is not None and len(positional) >= 3:
            return self._order_factory(signal, cycle_id, requested_size)
        return self._order_factory(signal, cycle_id)

    @staticmethod
    def _exit_fraction(index: int) -> Decimal:
        return (Decimal("0.4"), Decimal("0.3"), Decimal("0.3"))[index - 1]

    @classmethod
    def _partial_amount(cls, state: _ManagedPosition, current_size: Decimal, index: int) -> Decimal:
        fraction = cls._exit_fraction(index)
        if state.size_step is None:
            # Legacy injected mock gateways have no broker metadata; retain their
            # deterministic third-size behavior while production uses broker steps.
            return min(current_size, state.original_size / Decimal("3"))
        raw = state.original_size * fraction
        rounded = (raw / state.size_step).to_integral_value(
            rounding=ROUND_HALF_UP
        ) * state.size_step
        capped = (current_size / state.size_step).to_integral_value(
            rounding=ROUND_DOWN
        ) * state.size_step
        return min(rounded, capped)

    def _journal_event(self, cycle_id: str, event: str, **details: object) -> None:
        payload: dict[str, object] = {
            "cycle_id": cycle_id,
            "event": event,
            "label": EXPERIMENT_LABEL,
            **details,
        }
        self._journal(payload)

    def _refusal(
        self,
        cycle_id: str,
        reason: str,
        *,
        event: str,
        action: str = "NO_TRADE",
        **details: object,
    ) -> DemoOutcome:
        """Journal a refusal and emit one bounded safety alert for its reason."""

        self._journal_event(cycle_id, event, reason=reason, **details)
        if reason not in self._refusal_alerted:
            self._refusal_alerted.add(reason)
            epic = details.get("epic") or self._epic or "unknown"
            self._send_alert(f"entry refused for {epic}: {reason}")
        return self._outcome(cycle_id, action, reason)

    def _send_alert(self, message: str) -> None:
        prefix = "DRY-RUN: " if self._dry_run else ""
        self._alert(f"{prefix}{EXPERIMENT_LABEL}: {message}")

    @staticmethod
    def _outcome(cycle_id: str, action: str, reason: str) -> DemoOutcome:
        return DemoOutcome(action, reason, cycle_id)


__all__ = [
    "ClosedBar",
    "DemoOutcome",
    "EXPERIMENT_LABEL",
    "TaiDemoRefused",
    "TaiInstrumentRules",
    "TaiDemoRunner",
    "TaiDemoSettings",
    "TaiSignal",
]
