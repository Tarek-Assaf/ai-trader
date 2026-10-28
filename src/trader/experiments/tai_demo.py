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
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from trader.execution.brokers.base import Direction, OrderRequest, Position
from trader.execution.control import ControlMode
from trader.execution.manager import ExecutionPaused

LOGGER = logging.getLogger(__name__)
EXPERIMENT_LABEL = "Tai port, owner-directed demo experiment, loosely matched to the owner"
# Q-075 = A: refuse entries when the live quote consumes more than one quarter
# of the stop distance or two broker ticks, whichever is wider.
SPREAD_RISK_FRACTION = Decimal("0.25")
MIN_SPREAD_TICKS = Decimal("2")


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
    bid: Decimal | None = None
    ask: Decimal | None = None

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
        for name in ("bid", "ask"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite Decimal")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("ask must not be below bid")


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


@dataclass
class _EpicOutcomes:
    """In-memory per-epic outcome counters (reset at restart, never persisted)."""

    evaluated: int = 0
    no_signal: int = 0
    market_closed_or_break: int = 0
    stale_or_incomplete: int = 0
    refused: dict[str, int] = field(default_factory=dict)
    entry: int = 0
    exit: int = 0
    last_no_signal_gate: str | None = None


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
        self._last_exit_at: datetime | None = None
        self._spread_alerted: set[tuple[str, str]] = set()
        self._startup_blocked = False
        self._state_corrupt = False
        self._last_epic: str | None = epic
        self._last_bar: ClosedBar | None = None
        self._last_summary_hour: datetime | None = None
        self._outcomes: dict[str, _EpicOutcomes] = {}
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
        restore_exit = getattr(self._signal, "restore_exit", None)
        if callable(restore_exit):
            restore_exit(self._last_exit_at)
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
            raw_exit = payload.get("last_exit_at")
            if raw_exit is not None:
                self._last_exit_at = datetime.fromisoformat(str(raw_exit))
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
            "last_exit_at": (
                self._last_exit_at.astimezone(UTC).isoformat() if self._last_exit_at else None
            ),
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

        self._last_epic = bar.epic
        self._last_bar = bar
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
            self._journal_event(cycle_id, "position_open", epic=bar.epic)
            return self._outcome(cycle_id, "NO_TRADE", "one_position_rule")
        if self._startup_blocked:
            return self._outcome(cycle_id, "REJECTED", "startup_reconciliation_failed")
        signal = self._signal(bar)
        if signal is None:
            return self._outcome(cycle_id, "NO_TRADE", "no_signal")
        try:
            rules = self._rules_provider(bar.epic) if self._rules_provider is not None else None
            refusal = self._spread_refusal(signal, rules)
            if refusal is not None:
                reason, details = refusal
                self._journal_event(
                    cycle_id,
                    "order_refused",
                    epic=bar.epic,
                    reason=reason,
                    **details,
                )
                alert_key = (bar.epic, reason)
                if alert_key not in self._spread_alerted:
                    self._spread_alerted.add(alert_key)
                    self._send_alert(f"entry refused for {bar.epic}: {reason}")
                return self._outcome(cycle_id, "REJECTED", reason)
            requested_size = self._requested_size(rules)
            order = self._build_order(signal, cycle_id, requested_size)
        except (TaiDemoRefused, ValueError) as exc:
            LOGGER.warning(
                "tai demo order refused epic=%s cycle_id=%s reason=%s",
                bar.epic,
                cycle_id,
                str(exc),
            )
            self._journal_event(cycle_id, "order_refused", epic=bar.epic, reason=str(exc))
            return self._outcome(cycle_id, "REJECTED", "order_rules_unavailable")
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
            self._journal_event(cycle_id, "order_refused", reason="order_factory_mismatch")
            return self._outcome(cycle_id, "REJECTED", "order_factory_mismatch")
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
            self._send_alert(
                self._entry_text(
                    bar.epic,
                    signal,
                    order.size,
                    signal.entry,
                    self._managed[shadow_id].one_r_value,
                )
            )
            return self._outcome(cycle_id, "DRY_RUN", "order_would_submit")
        try:
            receipt = self._orders.submit(cycle_id, order)
        except ExecutionPaused as exc:
            reason = str(exc) or "execution_paused"
            self._journal_event(cycle_id, "guardian_veto", epic=bar.epic, reason=reason)
            self._send_alert(f"entry vetoed for {bar.epic}: {reason}")
            return self._outcome(cycle_id, "REJECTED", "guardian_veto")
        if receipt is None:
            self._journal_event(
                cycle_id, "guardian_veto", epic=bar.epic, reason="no_receipt_from_order_manager"
            )
            return self._outcome(cycle_id, "REJECTED", "guardian_veto")
        position = getattr(receipt, "position", None)
        if not isinstance(position, Position):
            self._journal_event(cycle_id, "order_refused", reason="receipt_missing_position")
            return self._outcome(cycle_id, "REJECTED", "receipt_missing_position")
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
        )
        self._journal_event(
            cycle_id,
            "entry",
            epic=bar.epic,
            deal_id=position.deal_id,
            one_r_value=self._managed[position.deal_id].one_r_value,
        )
        self._persist_state()
        self._send_alert(
            self._entry_text(
                bar.epic,
                signal,
                position.size,
                position.level,
                self._managed[position.deal_id].one_r_value,
            )
        )
        return self._outcome(cycle_id, "SUBMITTED", "entry_submitted")

    def _manage_exits(self, bar: ClosedBar, cycle_id: str) -> None:
        if self._dry_run:
            self._manage_shadow_exits(bar, cycle_id)
            return
        positions = tuple(self._positions.get_positions())
        present_ids = {position.deal_id for position in positions}
        for deal_id, tracked in tuple(self._managed.items()):
            if tracked.shadow or tracked.epic != bar.epic or deal_id in present_ids:
                continue
            self._managed.pop(deal_id, None)
            self._last_exit_at = bar.at
            note_exit = getattr(self._signal, "note_exit", None)
            if callable(note_exit):
                note_exit(bar.at)
            self._journal_event(
                cycle_id, "reconcile_closed", deal_id=deal_id, reason="broker_closed"
            )
            self._record_exit(bar.epic)
            self._send_alert(f"flat {bar.epic} broker_closed")
            self._persist_state()
        for position in positions:
            if position.epic != bar.epic:
                continue
            state = self._managed.get(position.deal_id)
            if state is None:
                continue
            current_size = position.size
            if 1 in state.hit and not state.entry_stop_moved:
                # An earlier attempt failed (price too close, broker busy): retry each bar.
                self._move_stop_to_entry(position, state, cycle_id)
            for index, target in enumerate(state.targets, start=1):
                if index in state.hit or not self._target_hit(state.direction, bar, target):
                    continue
                state.hit.add(index)
                if index == 1 and not state.entry_stop_moved:
                    self._move_stop_to_entry(position, state, cycle_id)
                if index == 3:
                    try:
                        self._positions.close_position(position.deal_id)
                    except Exception as exc:  # noqa: BLE001 - the broker target still closes it
                        self._exit_failed(position.deal_id, "close_failed", exc, cycle_id)
                        break
                    self._managed.pop(position.deal_id, None)
                    self._last_exit_at = bar.at
                    note_exit = getattr(self._signal, "note_exit", None)
                    if callable(note_exit):
                        note_exit(bar.at)
                    self._journal_event(
                        cycle_id, "exit", deal_id=position.deal_id, reason="target_3"
                    )
                    self._record_exit(bar.epic)
                    self._send_alert(
                        f"EXIT {bar.epic} TP3 {self._num(target)} reached\n"
                        f"Closed the remaining {self._num(current_size)}. Trade finished."
                    )
                    self._persist_state()
                    break
                close_partial = getattr(self._positions, "close_partial", None)
                if callable(close_partial):
                    amount = self._partial_amount(state, current_size, index)
                    if amount > 0 and amount < current_size:
                        try:
                            close_partial(position.deal_id, amount)
                        except Exception as exc:  # noqa: BLE001 - stop and target still protect it
                            self._exit_failed(position.deal_id, "scale_out_failed", exc, cycle_id)
                            continue
                        current_size -= amount
                        self._journal_event(
                            cycle_id,
                            "scale_out",
                            deal_id=position.deal_id,
                            target=index,
                            amount=amount,
                            fraction=str(self._exit_fraction(index)),
                        )
                        self._send_alert(
                            f"PARTIAL EXIT {bar.epic} TP{index} {self._num(target)} reached\n"
                            f"Closed {self._num(amount)}, remaining {self._num(current_size)}"
                        )
                    else:
                        self._journal_event(
                            cycle_id,
                            "scale_out_unavailable",
                            deal_id=position.deal_id,
                            target=index,
                        )
                self._persist_state()

    def _move_stop_to_entry(
        self, position: Position, state: _ManagedPosition, cycle_id: str
    ) -> None:
        """Move the protective stop to break-even; a refusal is logged and retried."""
        stop = state.entry
        if state.direction is Direction.BUY and stop > position.level:
            stop = position.level
        elif state.direction is Direction.SELL and stop < position.level:
            stop = position.level
        try:
            self._positions.modify_position(
                position.deal_id, stop_level=stop, profit_level=state.targets[2]
            )
        except Exception as exc:  # noqa: BLE001 - the original stop still protects it
            self._exit_failed(position.deal_id, "stop_to_entry_failed", exc, cycle_id)
            return
        state.entry_stop_moved = True
        self._journal_event(cycle_id, "stop_to_entry", deal_id=position.deal_id)
        self._send_alert(
            f"STOP MOVED TO ENTRY {position.epic} at {self._num(stop)}\n"
            "Trade is now risk-free; TP3 stays as the target."
        )

    def _exit_failed(self, deal_id: str, event: str, exc: Exception, cycle_id: str) -> None:
        LOGGER.warning("tai demo %s deal=%s reason=%s", event, deal_id, type(exc).__name__)
        self._journal_event(cycle_id, event, deal_id=deal_id, reason=str(exc)[:200])
        key = (deal_id, event)
        if key not in self._spread_alerted:
            self._spread_alerted.add(key)
            self._send_alert(f"{event} for {deal_id}: {str(exc)[:120]}")

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
                    self._last_exit_at = bar.at
                    note_exit = getattr(self._signal, "note_exit", None)
                    if callable(note_exit):
                        note_exit(bar.at)
                    self._journal_event(cycle_id, "exit", deal_id=deal_id, reason="target_3")
                    self._record_exit(bar.epic)
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
            self._last_exit_at = bar.at
            note_exit = getattr(self._signal, "note_exit", None)
            if callable(note_exit):
                note_exit(bar.at)
            self._journal_event(cycle_id, "exit", deal_id=deal_id, reason=reason)
            self._record_exit(bar.epic)
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

    @staticmethod
    def _num(value: object) -> str:
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except Exception:  # noqa: BLE001 - display only
            return str(value)

    def _entry_text(
        self,
        epic: str,
        signal: TaiSignal,
        size: object,
        fill: object,
        one_r_value: Decimal | None,
        *,
        prefix: str = "ENTRY",
    ) -> str:
        n = self._num
        tp1, tp2, tp3 = signal.levels()
        risk = abs(signal.entry - signal.stop)
        risk_usd = f" (1R = ${n(one_r_value.quantize(Decimal('0.01')))})" if one_r_value else ""
        return (
            f"{prefix} {signal.direction.value} {epic}\n"
            f"Size: {n(size)}  Entry: {n(fill)}\n"
            f"SL: {n(signal.stop)}  risk {n(risk)} pts{risk_usd}\n"
            f"TP1: {n(tp1)}  TP2: {n(tp2)}  TP3: {n(tp3)}\n"
            "Plan: 40% at TP1 (stop to entry), 30% at TP2, 30% at TP3"
        )

    def _send_alert(self, message: str) -> None:
        prefix = "DRY-RUN: " if self._dry_run else ""
        self._alert(f"{prefix}{EXPERIMENT_LABEL}: {message}")

    @staticmethod
    def _spread_refusal(
        signal: TaiSignal, rules: TaiInstrumentRules | None
    ) -> tuple[str, dict[str, Decimal]] | None:
        """Return a fail-closed spread refusal, preserving legacy no-rules tests."""
        if rules is None:
            return None
        if rules.tick_size is None or rules.bid is None or rules.ask is None:
            return ("spread_quote_unavailable", {})
        spread = rules.ask - rules.bid
        stop_distance = abs(signal.entry - signal.stop)
        limit = max(SPREAD_RISK_FRACTION * stop_distance, MIN_SPREAD_TICKS * rules.tick_size)
        if spread <= limit:
            return None
        return (
            "spread_too_wide",
            {
                "bid": rules.bid,
                "ask": rules.ask,
                "spread": spread,
                "spread_limit": limit,
                "stop_distance": stop_distance,
                "tick_size": rules.tick_size,
            },
        )

    @staticmethod
    def _blank_outcomes() -> _EpicOutcomes:
        return _EpicOutcomes()

    def health_snapshot(self) -> dict[str, dict[str, object]]:
        """Return secret-free outcome counters grouped by epic."""
        ha_mode = getattr(self._signal, "ha_mode", None)
        return {
            epic: {
                "evaluated": values.evaluated,
                "no_signal": values.no_signal,
                "market_closed_or_break": values.market_closed_or_break,
                "stale_or_incomplete": values.stale_or_incomplete,
                "refused": dict(values.refused),
                "entry": values.entry,
                "exit": values.exit,
                "last_no_signal_gate": values.last_no_signal_gate,
                "ha_mode": ha_mode if isinstance(ha_mode, str) else None,
            }
            for epic, values in self._outcomes.items()
        }

    def _outcome(self, cycle_id: str, action: str, reason: str) -> DemoOutcome:
        epic = self._last_epic or self._epic or "unknown"
        bar = self._last_bar
        if bar is not None:
            audit = {
                "at": bar.at.astimezone(UTC).isoformat(),
                "epic": bar.epic,
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "timeframe_minutes": bar.timeframe_minutes,
                "closed": bar.closed,
                "market_open": bar.market_open,
                "in_break": bar.in_break,
                "volume": str(bar.volume),
            }
            fingerprint = sha256(
                json.dumps(audit, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            self._journal_event(
                cycle_id,
                "decision",
                action=action,
                reason=reason,
                bar_fingerprint=fingerprint,
                **audit,
            )
        values = self._outcomes.setdefault(epic, self._blank_outcomes())
        values.evaluated += 1
        if reason == "no_signal":
            values.no_signal += 1
            gate = getattr(self._signal, "last_failure_gate", None)
            values.last_no_signal_gate = gate if isinstance(gate, str) else None
        elif reason == "market_closed_or_blackout":
            values.market_closed_or_break += 1
        elif reason in {"bar_not_closed", "feed_stale"}:
            values.stale_or_incomplete += 1
        elif reason in {"entry_submitted", "order_would_submit"}:
            values.entry += 1
        elif action == "REJECTED":
            values.refused[reason] = values.refused.get(reason, 0) + 1
        self._log_summary_if_due(epic, values)
        return DemoOutcome(action, reason, cycle_id)

    def _record_exit(self, epic: str) -> None:
        self._outcomes.setdefault(epic, self._blank_outcomes()).exit += 1

    def _log_summary_if_due(self, epic: str, values: _EpicOutcomes) -> None:
        hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        if self._last_summary_hour == hour:
            return
        self._last_summary_hour = hour
        LOGGER.info(
            "tai demo outcome summary epic=%s evaluated=%s no_signal=%s "
            "market_closed_or_break=%s stale_or_incomplete=%s refused=%s entry=%s exit=%s",
            epic,
            values.evaluated,
            values.no_signal,
            values.market_closed_or_break,
            values.stale_or_incomplete,
            values.refused,
            values.entry,
            values.exit,
        )


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
