"""Deterministic in-memory paper broker with explicit, injectable execution costs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock
from types import MappingProxyType

from trader.execution.brokers.base import (
    BrokerError,
    BrokerProtocolError,
    Direction,
    OrderReceipt,
    OrderRequest,
    Position,
)


def _valid_decimal(value: object, *, allow_zero: bool = False) -> bool:
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and (value >= 0 if allow_zero else value > 0)
    )


@dataclass(frozen=True, slots=True)
class PaperQuote:
    """Latest executable bid/ask quote supplied by the caller."""

    bid: Decimal
    ask: Decimal

    def __post_init__(self) -> None:
        if not _valid_decimal(self.bid) or not _valid_decimal(self.ask) or self.ask < self.bid:
            raise ValueError("quote must have positive finite bid and ask with ask >= bid")


@dataclass(frozen=True, slots=True)
class PaperExecutionCosts:
    """Per-epic fallback costs used when no current quote is available.

    Values are price points, not pips or currency. No market costs are guessed:
    callers must supply explicit spread and slippage inputs until T-017 provides
    recorded, instrument-specific calibration.
    """

    spread_points: Mapping[str, Decimal]
    slippage_points: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        spreads = dict(self.spread_points)
        slippage = dict(self.slippage_points)
        if not spreads or spreads.keys() != slippage.keys():
            raise ValueError("spread and slippage costs must cover the same epics")
        for epic, spread in spreads.items():
            if (
                not isinstance(epic, str)
                or not epic.strip()
                or not _valid_decimal(spread, allow_zero=True)
            ):
                raise ValueError(
                    "spread costs require non-empty epics and finite non-negative points"
                )
        for epic, amount in slippage.items():
            if not _valid_decimal(amount, allow_zero=True):
                raise ValueError(f"slippage cost for {epic} must be finite and non-negative")
        object.__setattr__(self, "spread_points", MappingProxyType(spreads))
        object.__setattr__(self, "slippage_points", MappingProxyType(slippage))

    def for_epic(self, epic: str) -> tuple[Decimal, Decimal]:
        try:
            return self.spread_points[epic], self.slippage_points[epic]
        except KeyError:
            raise BrokerProtocolError(
                "paper execution costs are not configured for this epic"
            ) from None


class PaperBroker:
    """A thread-safe BrokerAdapter simulator; it never connects to a remote broker."""

    def __init__(
        self,
        *,
        starting_cash: Decimal,
        currency: str,
        costs: PaperExecutionCosts,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not _valid_decimal(starting_cash):
            raise ValueError("starting_cash must be a positive finite Decimal")
        if not isinstance(currency, str) or not currency.strip():
            raise ValueError("currency must be non-empty")
        self._cash = starting_cash
        self._currency = currency.strip().upper()
        self._costs = costs
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = RLock()
        self._positions: dict[str, Position] = {}
        self._quotes: dict[str, PaperQuote] = {}
        self._used_order_ids: set[str] = set()
        self._activity: list[dict[str, object]] = []
        self._next_id = 1

    def get_account(self) -> Mapping[str, object]:
        with self._lock:
            unrealized = sum(
                (self._unrealized(position) for position in self._positions.values()),
                start=Decimal("0"),
            )
            return {
                "balance": self._cash,
                "equity": self._cash + unrealized,
                "profit": unrealized,
                "currency": self._currency,
                "financing": Decimal("0"),
                "financing_status": "placeholder_not_modeled",
            }

    def get_positions(self) -> Sequence[Position]:
        with self._lock:
            return tuple(self._positions.values())

    def get_open_orders(self) -> Sequence[Mapping[str, object]]:
        # PaperBroker supports immediate market fills only.
        return ()

    def get_market_details(self, epic: str) -> Mapping[str, object]:
        spread, slippage = self._costs.for_epic(epic)
        return {
            "epic": epic,
            "spread_points": spread,
            "slippage_points": slippage,
            "cost_source": "explicit_paper_execution_costs",
            "financing_status": "placeholder_not_modeled",
        }

    def get_activity(self, start: datetime, end: datetime) -> Sequence[Mapping[str, object]]:
        if not self._aware(start) or not self._aware(end) or start > end:
            raise ValueError("activity range must be ordered timezone-aware datetimes")
        with self._lock:
            return tuple(
                dict(item)
                for item in self._activity
                if start <= item["timestamp"] <= end  # type: ignore[operator]
            )

    def place_order(self, order: OrderRequest) -> OrderReceipt:
        with self._lock:
            if order.client_order_id in self._used_order_ids:
                raise BrokerError("client_order_id has already been submitted")
            spread, slippage = self._costs.for_epic(order.epic)
            quote = self._quotes.get(order.epic)
            fill = self._entry_fill(order, quote, spread, slippage)
            self._validate_bracket(order, fill)
            deal_id = f"paper-{self._next_id:08d}"
            reference = f"paper-open-{self._next_id:08d}"
            position = Position(
                deal_id=deal_id,
                epic=order.epic,
                direction=order.direction,
                size=order.size,
                level=fill,
                stop_level=order.stop_level,
                profit_level=order.profit_level,
            )
            # Reservation occurs immediately before the deterministic in-memory write.
            self._used_order_ids.add(order.client_order_id)
            self._positions[deal_id] = position
            self._next_id += 1
            self._record(
                action="opened",
                position=position,
                size=order.size,
                level=fill,
                profit=Decimal("0"),
                reference=reference,
                reason="market",
            )
            return OrderReceipt(order.client_order_id, reference, deal_id, position)

    def modify_position(
        self, deal_id: str, *, stop_level: Decimal, profit_level: Decimal | None = None
    ) -> str:
        if not _valid_decimal(stop_level):
            raise ValueError("stop_level must be a positive finite Decimal")
        with self._lock:
            existing = self._positions.get(deal_id)
            if existing is None or existing.stop_level is None:
                raise BrokerError("position is not open with a protective stop")
            if existing.direction is Direction.BUY:
                if stop_level < existing.stop_level:
                    raise BrokerError("BUY stop modifications may not loosen the protective stop")
                if stop_level >= self._mark_for_position(existing):
                    raise ValueError("BUY stop must remain below the current market")
                if profit_level is not None and profit_level <= self._mark_for_position(existing):
                    raise ValueError("BUY profit target must remain above the current market")
            else:
                if stop_level > existing.stop_level:
                    raise BrokerError("SELL stop modifications may not loosen the protective stop")
                if stop_level <= self._mark_for_position(existing):
                    raise ValueError("SELL stop must remain above the current market")
                if profit_level is not None and profit_level >= self._mark_for_position(existing):
                    raise ValueError("SELL profit target must remain below the current market")
            if profit_level is not None and not _valid_decimal(profit_level):
                raise ValueError("profit_level must be a positive finite Decimal")
            updated = replace(
                existing,
                stop_level=stop_level,
                profit_level=(profit_level if profit_level is not None else existing.profit_level),
            )
            self._positions[deal_id] = updated
            reference = self._reference("modify")
            self._record(
                action="modified",
                position=updated,
                size=updated.size,
                level=self._mark_for_position(updated),
                profit=Decimal("0"),
                reference=reference,
                reason="protective_levels",
            )
            return reference

    def close_position(self, deal_id: str) -> str:
        with self._lock:
            position = self._positions.get(deal_id)
            if position is None:
                raise BrokerError("position is not open")
            self._close_locked(position, position.size, reason="manual")
            return f"paper-close-{self._next_id - 1:08d}"

    def close_partial(self, deal_id: str, size: Decimal) -> str:
        if not _valid_decimal(size):
            raise ValueError("partial close size must be a positive finite Decimal")
        with self._lock:
            position = self._positions.get(deal_id)
            if position is None or size >= position.size:
                raise ValueError("partial close must be smaller than an open position")
            self._close_locked(position, size, reason="partial")
            return f"paper-partial-{self._next_id - 1:08d}"

    def cancel_working_order(self, deal_id: str) -> str:
        if not deal_id.strip():
            raise ValueError("deal_id must not be empty")
        raise BrokerError("PaperBroker has no working orders to cancel")

    def update_quote(self, epic: str, *, bid: Decimal, ask: Decimal) -> tuple[str, ...]:
        """Update the executable quote and trigger attached stops/targets if crossed."""
        quote = PaperQuote(bid=bid, ask=ask)
        with self._lock:
            self._costs.for_epic(epic)
            self._quotes[epic] = quote
            closed: list[str] = []
            for position in tuple(self._positions.values()):
                if position.epic != epic:
                    continue
                exit_reference = self._exit_reference(position, quote)
                reason = self._trigger_reason(position, exit_reference)
                if reason is not None:
                    self._close_locked(position, position.size, reason=reason)
                    closed.append(position.deal_id)
            return tuple(closed)

    def _close_locked(self, position: Position, size: Decimal, *, reason: str) -> None:
        quote = self._quotes.get(position.epic)
        spread, slippage = self._costs.for_epic(position.epic)
        reference = self._exit_reference(position, quote)
        fill = self._exit_fill(position.direction, reference, quote, spread, slippage)
        sign = Decimal("1") if position.direction is Direction.BUY else Decimal("-1")
        realized = (fill - position.level) * size * sign
        remaining = position.size - size
        if remaining:
            self._positions[position.deal_id] = replace(position, size=remaining)
        else:
            del self._positions[position.deal_id]
        self._cash += realized
        ref = self._reference("close")
        self._record(
            action="closed" if not remaining else "partially_closed",
            position=position,
            size=size,
            level=fill,
            profit=realized,
            reference=ref,
            reason=reason,
        )

    def _unrealized(self, position: Position) -> Decimal:
        quote = self._quotes.get(position.epic)
        spread, slippage = self._costs.for_epic(position.epic)
        reference = self._exit_reference(position, quote)
        close_fill = self._exit_fill(position.direction, reference, quote, spread, slippage)
        sign = Decimal("1") if position.direction is Direction.BUY else Decimal("-1")
        return (close_fill - position.level) * position.size * sign

    @staticmethod
    def _entry_fill(
        order: OrderRequest,
        quote: PaperQuote | None,
        spread: Decimal,
        slippage: Decimal,
    ) -> Decimal:
        if quote is not None:
            reference = quote.ask if order.direction is Direction.BUY else quote.bid
            return (
                reference + slippage if order.direction is Direction.BUY else reference - slippage
            )
        half_spread = spread / Decimal("2")
        adverse_cost = half_spread + slippage
        return (
            order.reference_price + adverse_cost
            if order.direction is Direction.BUY
            else order.reference_price - adverse_cost
        )

    @staticmethod
    def _exit_reference(position: Position, quote: PaperQuote | None) -> Decimal:
        if quote is None:
            return position.level
        return quote.bid if position.direction is Direction.BUY else quote.ask

    @staticmethod
    def _exit_fill(
        direction: Direction,
        reference: Decimal,
        quote: PaperQuote | None,
        spread: Decimal,
        slippage: Decimal,
    ) -> Decimal:
        # A current quote already supplies the executable bid or ask side.
        cost = slippage if quote is not None else spread / Decimal("2") + slippage
        return reference - cost if direction is Direction.BUY else reference + cost

    def _mark_for_position(self, position: Position) -> Decimal:
        return self._exit_reference(position, self._quotes.get(position.epic))

    @staticmethod
    def _validate_bracket(order: OrderRequest, fill: Decimal) -> None:
        if order.direction is Direction.BUY:
            if order.stop_level >= fill:
                raise BrokerProtocolError("BUY protective stop is not below the simulated fill")
            if order.profit_level is not None and order.profit_level <= fill:
                raise BrokerProtocolError("BUY profit target is not above the simulated fill")
        else:
            if order.stop_level <= fill:
                raise BrokerProtocolError("SELL protective stop is not above the simulated fill")
            if order.profit_level is not None and order.profit_level >= fill:
                raise BrokerProtocolError("SELL profit target is not below the simulated fill")

    @staticmethod
    def _trigger_reason(position: Position, exit_reference: Decimal) -> str | None:
        # Protective stops win if a single gap crosses both bracket levels.
        if position.direction is Direction.BUY:
            if position.stop_level is not None and exit_reference <= position.stop_level:
                return "stop_loss"
            if position.profit_level is not None and exit_reference >= position.profit_level:
                return "take_profit"
        else:
            if position.stop_level is not None and exit_reference >= position.stop_level:
                return "stop_loss"
            if position.profit_level is not None and exit_reference <= position.profit_level:
                return "take_profit"
        return None

    def _record(
        self,
        *,
        action: str,
        position: Position,
        size: Decimal,
        level: Decimal,
        profit: Decimal,
        reference: str,
        reason: str,
    ) -> None:
        self._activity.append(
            {
                "timestamp": self._now(),
                "action": action,
                "dealId": position.deal_id,
                "dealReference": reference,
                "epic": position.epic,
                "direction": position.direction.value,
                "size": size,
                "level": level,
                "profit": profit,
                "financing": Decimal("0"),
                "financing_status": "placeholder_not_modeled",
                "reason": reason,
            }
        )

    def _reference(self, action: str) -> str:
        reference = f"paper-{action}-{self._next_id:08d}"
        self._next_id += 1
        return reference

    def _now(self) -> datetime:
        value = self._clock()
        if not self._aware(value):
            raise BrokerProtocolError("paper broker clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _aware(value: datetime) -> bool:
        return value.tzinfo is not None and value.utcoffset() is not None
