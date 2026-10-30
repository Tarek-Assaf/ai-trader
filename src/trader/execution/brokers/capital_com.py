"""Demo-only Capital.com broker adapter with fail-closed order handling."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

from trader.execution.brokers.base import (
    BrokerError,
    BrokerProtocolError,
    Direction,
    OrderReceipt,
    OrderRejected,
    OrderRequest,
    Position,
    TradingDisabledError,
)

_REFUSED_STATUS = frozenset({400, 403, 404, 422})


class _CapitalComClient(Protocol):
    @property
    def environment(self) -> str: ...

    def account_info(self) -> Mapping[str, object]: ...

    def market_details(self, epic: str) -> Mapping[str, object]: ...

    def get_positions(self) -> list[Mapping[str, object]]: ...

    def get_working_orders(self) -> list[Mapping[str, object]]: ...

    def get_activity(self, start: datetime, end: datetime) -> list[Mapping[str, object]]: ...

    def open_position(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    def reduce_position(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    def confirm_deal(self, deal_reference: str) -> Mapping[str, object]: ...

    def modify_position(
        self, deal_id: str, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def close_position(self, deal_id: str) -> Mapping[str, object]: ...

    def cancel_working_order(self, deal_id: str) -> Mapping[str, object]: ...


def _decimal(value: object, field: str, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float | Decimal):
        raise BrokerProtocolError(f"Capital.com response has invalid {field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BrokerProtocolError(f"Capital.com response has invalid {field}") from None
    if not result.is_finite():
        raise BrokerProtocolError(f"Capital.com response has invalid {field}")
    return result


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrokerProtocolError(f"Capital.com response is missing {field}")
    return value


def _object(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BrokerProtocolError(f"Capital.com response has invalid {field}")
    return value


def _position(payload: Mapping[str, object]) -> Position:
    position = _object(payload.get("position"), "position")
    market = _object(payload.get("market"), "market")
    direction_text = _required_text(position.get("direction"), "position.direction")
    try:
        direction = Direction(direction_text)
    except ValueError:
        raise BrokerProtocolError("Capital.com response has invalid position.direction") from None
    size = _decimal(position.get("size"), "position.size")
    level = _decimal(position.get("level"), "position.level")
    assert size is not None and level is not None
    if size <= 0 or level <= 0:
        raise BrokerProtocolError("Capital.com response has non-positive position values")
    return Position(
        deal_id=_required_text(position.get("dealId"), "position.dealId"),
        epic=_required_text(market.get("epic"), "market.epic"),
        direction=direction,
        size=size,
        level=level,
        stop_level=_decimal(position.get("stopLevel"), "position.stopLevel", optional=True),
        profit_level=_decimal(position.get("profitLevel"), "position.profitLevel", optional=True),
    )


class CapitalComBroker:
    """Capital.com adapter that cannot send orders unless explicitly enabled.

    It is permanently restricted to the demo host. Order enabling is a separate
    explicit constructor choice; application wiring remains disabled until the
    owner authorizes the demo round trip. Callers must persist the client order
    id and intent before invoking this adapter (the durable order manager is T-026).
    """

    def __init__(self, client: _CapitalComClient, *, enable_demo_orders: bool = False) -> None:
        if client.environment != "demo":
            raise TradingDisabledError("CapitalComBroker only supports the demo environment")
        self._client = client
        self._enable_demo_orders = enable_demo_orders
        self._write_lock = threading.Lock()
        self._used_order_ids: set[str] = set()

    def get_positions(self) -> Sequence[Position]:
        return tuple(_position(item) for item in self._client.get_positions())

    def get_open_orders(self) -> Sequence[Mapping[str, object]]:
        return tuple(self._client.get_working_orders())

    def get_working_orders(self) -> Sequence[Mapping[str, object]]:
        """Compatibility name for the Capital.com working-orders endpoint."""
        return self.get_open_orders()

    def get_account(self) -> Mapping[str, object]:
        return self._client.account_info()

    def get_market_details(self, epic: str) -> Mapping[str, object]:
        if not epic.strip():
            raise ValueError("epic must not be empty")
        return self._client.market_details(epic)

    def get_activity(self, start: datetime, end: datetime) -> Sequence[Mapping[str, object]]:
        return tuple(self._client.get_activity(start, end))

    def place_order(self, order: OrderRequest) -> OrderReceipt:
        if not self._enable_demo_orders:
            raise TradingDisabledError("demo order placement is disabled")
        if order.size <= 0 or not order.size.is_finite():
            raise ValueError("order size must be finite and positive")
        with self._write_lock:
            if order.client_order_id in self._used_order_ids:
                raise BrokerError("client_order_id has already been submitted")
            # Reserve before the network call; ambiguous failures must not be retried
            # with the same in-process id. T-026 provides durable reservation.
            self._used_order_ids.add(order.client_order_id)
            payload: dict[str, object] = {
                "epic": order.epic,
                "direction": order.direction.value,
                "size": float(order.size),
                "guaranteedStop": False,
                "stopLevel": float(order.stop_level),
            }
            if order.profit_level is not None:
                payload["profitLevel"] = float(order.profit_level)
            try:
                created = self._client.open_position(payload)
            except Exception as exc:
                # A 4xx on the order request itself means Capital.com did not accept it, so no
                # position exists. Anything else (timeout, 5xx) may have been accepted, so it
                # stays ambiguous.
                if getattr(exc, "status_code", None) in _REFUSED_STATUS:
                    code = getattr(exc, "error_code", None)
                    raise OrderRejected(code if isinstance(code, str) else None) from None
                raise
            reference = _required_text(created.get("dealReference"), "dealReference")
            confirmation = self._client.confirm_deal(reference)
            self._require_accepted(confirmation, "open")
            deals = confirmation.get("affectedDeals")
            if not isinstance(deals, list) or len(deals) != 1:
                raise BrokerProtocolError("Capital.com open confirmation is ambiguous")
            affected = _object(deals[0], "affectedDeals[0]")
            if affected.get("status") != "OPENED":
                raise BrokerProtocolError("Capital.com did not confirm the position opened")
            deal_id = _required_text(affected.get("dealId"), "affectedDeals[0].dealId")
            positions = self._client.get_positions()
            matches: list[Position] = []
            for item in positions:
                position_data = item.get("position")
                if isinstance(position_data, Mapping) and position_data.get("dealId") == deal_id:
                    matches.append(_position(item))
            if len(matches) != 1:
                raise BrokerProtocolError("Capital.com opened position was not uniquely confirmed")
            opened = matches[0]
            if (
                opened.epic != order.epic
                or opened.direction is not order.direction
                or opened.size != order.size
                or opened.stop_level != order.stop_level
            ):
                # The order was submitted with the stop in the same request. If the
                # broker's confirmed state differs, immediately request a close.
                try:
                    close = self._client.close_position(deal_id)
                    close_reference = _required_text(
                        close.get("dealReference"), "close dealReference"
                    )
                    self._require_accepted(
                        self._client.confirm_deal(close_reference), "protective close"
                    )
                except Exception:
                    raise BrokerProtocolError(
                        "Capital.com did not confirm the protective stop; close status is uncertain"
                    ) from None
                raise BrokerProtocolError(
                    "Capital.com did not retain the requested stop; a close was requested"
                )
            return OrderReceipt(order.client_order_id, reference, deal_id, opened)

    @staticmethod
    def _require_accepted(reply: Mapping[str, object], operation: str) -> None:
        status = reply.get("dealStatus")
        if status != "ACCEPTED":
            raise BrokerProtocolError(f"Capital.com did not confirm {operation}")

    def modify_position(
        self, deal_id: str, *, stop_level: Decimal, profit_level: Decimal | None = None
    ) -> str:
        if (
            not deal_id.strip()
            or not isinstance(stop_level, Decimal)
            or not stop_level.is_finite()
            or stop_level <= 0
        ):
            raise ValueError("deal_id and a positive finite Decimal stop_level are required")
        with self._write_lock:
            existing = next((p for p in self.get_positions() if p.deal_id == deal_id), None)
            if existing is None or existing.stop_level is None:
                raise BrokerError("position is not open with a confirmed protective stop")
            if existing.direction is Direction.BUY and stop_level < existing.stop_level:
                raise BrokerError("BUY stop modifications may not loosen the protective stop")
            if existing.direction is Direction.SELL and stop_level > existing.stop_level:
                raise BrokerError("SELL stop modifications may not loosen the protective stop")
            if existing.direction is Direction.BUY and stop_level >= existing.level:
                raise ValueError("BUY stop must remain below the position level")
            if existing.direction is Direction.SELL and stop_level <= existing.level:
                raise ValueError("SELL stop must remain above the position level")
            payload: dict[str, object] = {
                "guaranteedStop": False,
                "stopLevel": float(stop_level),
            }
            if profit_level is not None:
                if (
                    not isinstance(profit_level, Decimal)
                    or not profit_level.is_finite()
                    or profit_level <= 0
                ):
                    raise ValueError("profit_level must be finite and positive")
                payload["profitLevel"] = float(profit_level)
            reply = self._client.modify_position(deal_id, payload)
            reference = _required_text(reply.get("dealReference"), "dealReference")
            self._require_accepted(self._client.confirm_deal(reference), "position modification")
            updated = next((p for p in self.get_positions() if p.deal_id == deal_id), None)
            if updated is None or updated.stop_level != stop_level:
                raise BrokerProtocolError("Capital.com did not confirm the modified stop")
            return reference

    def close_position(self, deal_id: str) -> str:
        if not deal_id.strip():
            raise ValueError("deal_id must not be empty")
        with self._write_lock:
            reply = self._client.close_position(deal_id)
            reference = _required_text(reply.get("dealReference"), "dealReference")
            self._require_accepted(self._client.confirm_deal(reference), "position close")
            return reference

    def close_partial(self, deal_id: str, size: Decimal) -> str:
        """Reduce a position by ``size``.

        Capital.com has no partial-close call. With hedging off, an opposite-direction
        market order of that size nets against the open position. The result is verified
        from the broker's own position list; any other outcome is undone and raises.
        """
        if not deal_id.strip() or not isinstance(size, Decimal) or not size.is_finite():
            raise ValueError("deal_id and a finite Decimal size are required")
        if size <= 0:
            raise ValueError("partial close size must be positive")
        if not self._enable_demo_orders:
            raise TradingDisabledError("demo order placement is disabled")
        with self._write_lock:
            before = {p.deal_id: p for p in self.get_positions()}
            existing = before.get(deal_id)
            if existing is None:
                raise BrokerError("position is not open")
            if size >= existing.size:
                raise ValueError("partial close must be smaller than the open position")
            opposite = Direction.SELL if existing.direction is Direction.BUY else Direction.BUY
            created = self._client.reduce_position(
                {
                    "epic": existing.epic,
                    "direction": opposite.value,
                    "size": float(size),
                    "guaranteedStop": False,
                }
            )
            reference = _required_text(created.get("dealReference"), "dealReference")
            self._require_accepted(self._client.confirm_deal(reference), "partial close")
            after = {p.deal_id: p for p in self.get_positions()}
            reduced = after.get(deal_id)
            stray = [
                p
                for key, p in after.items()
                if key not in before and p.epic == existing.epic and p.direction is opposite
            ]
            if (
                reduced is not None
                and not stray
                and reduced.size == existing.size - size
                and reduced.direction is existing.direction
                and reduced.stop_level == existing.stop_level
            ):
                return reference
            for extra in stray:
                try:
                    self._client.close_position(extra.deal_id)
                except Exception:
                    pass
            raise BrokerProtocolError(
                "Capital.com did not net the opposite order into the open position; "
                "stray opposite positions were closed"
            )

    def cancel_working_order(self, deal_id: str) -> str:
        if not deal_id.strip():
            raise ValueError("deal_id must not be empty")
        with self._write_lock:
            reply = self._client.cancel_working_order(deal_id)
            reference = _required_text(reply.get("dealReference"), "dealReference")
            self._require_accepted(
                self._client.confirm_deal(reference), "working-order cancellation"
            )
            return reference
