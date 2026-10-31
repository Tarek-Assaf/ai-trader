"""Typed broker contracts shared by paper and remote broker adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class BrokerError(RuntimeError):
    """Base class for sanitized broker adapter failures."""


class BrokerProtocolError(BrokerError):
    """Raised when a broker reply is missing required fields or is inconsistent."""


class OrderRejected(BrokerError):
    """The broker refused the order request outright, so no position was opened."""

    def __init__(self, code: str | None = None) -> None:
        self.code = code
        super().__init__(f"the broker refused the order: {code or 'no reason code'}")


_TRANSIENT_STATUS = frozenset({408, 425, 429})


def is_transient_broker_error(exc: BaseException) -> bool:
    """True when the broker could not be reached right now (network, timeout, 429, 5xx).

    Duck-typed on ``status_code`` so broker adapters need no shared import: a transport error
    has ``status_code`` None. A refusal or a malformed reply is not transient.
    """
    if not hasattr(exc, "status_code"):
        return False
    status = exc.status_code
    if status is None:
        return True
    return isinstance(status, int) and (status in _TRANSIENT_STATUS or status >= 500)


class TradingDisabledError(BrokerError):
    """Raised when an order path is not explicitly enabled."""


class Direction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """A fully sized, risk-approved market order with a required protective stop.

    The size must already be produced by the deterministic position sizer.
    The reference price is used only to reject a stop on the unsafe side of entry.
    Durable idempotency reservation belongs to the order manager (T-026).
    """

    client_order_id: str
    epic: str
    direction: Direction
    size: Decimal
    reference_price: Decimal
    stop_level: Decimal
    profit_level: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.direction, Direction):
            raise ValueError("direction must be BUY or SELL")
        if not self.client_order_id.strip():
            raise ValueError("client_order_id must not be empty")
        if not self.epic.strip():
            raise ValueError("epic must not be empty")
        for name in ("size", "reference_price", "stop_level"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be a finite positive Decimal")
        if self.profit_level is not None and (
            not isinstance(self.profit_level, Decimal)
            or not self.profit_level.is_finite()
            or self.profit_level <= 0
        ):
            raise ValueError("profit_level must be finite and greater than zero")
        if self.direction is Direction.BUY:
            if self.stop_level >= self.reference_price:
                raise ValueError("BUY stop_level must be below reference_price")
            if self.profit_level is not None and self.profit_level <= self.reference_price:
                raise ValueError("BUY profit_level must be above reference_price")
        else:
            if self.stop_level <= self.reference_price:
                raise ValueError("SELL stop_level must be above reference_price")
            if self.profit_level is not None and self.profit_level >= self.reference_price:
                raise ValueError("SELL profit_level must be below reference_price")


@dataclass(frozen=True, slots=True)
class Position:
    deal_id: str
    epic: str
    direction: Direction
    size: Decimal
    level: Decimal
    stop_level: Decimal | None
    profit_level: Decimal | None


@dataclass(frozen=True, slots=True)
class OrderReceipt:
    client_order_id: str
    deal_reference: str
    deal_id: str
    position: Position


class BrokerAdapter(Protocol):
    """Deterministic execution boundary. Implementations must fail closed."""

    def get_account(self) -> Mapping[str, object]: ...

    def get_positions(self) -> Sequence[Position]: ...

    def get_open_orders(self) -> Sequence[Mapping[str, object]]: ...

    def get_market_details(self, epic: str) -> Mapping[str, object]: ...

    def get_activity(self, start: datetime, end: datetime) -> Sequence[Mapping[str, object]]: ...

    def place_order(self, order: OrderRequest) -> OrderReceipt: ...

    def modify_position(
        self, deal_id: str, *, stop_level: Decimal, profit_level: Decimal | None = None
    ) -> str: ...

    def close_position(self, deal_id: str) -> str: ...

    def cancel_working_order(self, deal_id: str) -> str: ...
