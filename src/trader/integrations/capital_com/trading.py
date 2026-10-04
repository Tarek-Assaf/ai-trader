"""Trading-only Capital.com API surface, kept separate from market-data reads."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from trader.integrations.capital_com.client import CapitalComClient, CapitalComError


class CapitalComTradingClient(CapitalComClient):
    """Authenticated Capital.com client with explicit position/order methods.

    Market-data callers should use CapitalComClient, which remains GET-only.
    """

    def _request_trading(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        json: Mapping[str, object] | None = None,
        history: bool = False,
    ) -> Any:
        for auth_attempt in range(2):
            headers = self._auth_headers()
            response = self._send(
                method, path, params=params, json=json, headers=headers, history=history
            )
            if response.status_code != 401:
                try:
                    return response.json()
                except ValueError:
                    raise CapitalComError(response.status_code, path) from None
            with self._session_lock:
                self._cst = None
                self._security_token = None
            if auth_attempt == 1:
                raise CapitalComError(401, path)
        raise AssertionError("unreachable")

    def get_positions(self) -> list[Mapping[str, Any]]:
        """Return validated open-position records."""
        payload = self._request_trading("GET", "positions")
        positions = payload.get("positions") if isinstance(payload, Mapping) else None
        if not isinstance(positions, list) or any(
            not isinstance(item, Mapping) for item in positions
        ):
            raise CapitalComError(200, "positions")
        return positions

    def get_working_orders(self) -> list[Mapping[str, Any]]:
        """Return validated working-order records."""
        payload = self._request_trading("GET", "workingorders")
        orders = payload.get("workingOrders") if isinstance(payload, Mapping) else None
        if not isinstance(orders, list) or any(not isinstance(item, Mapping) for item in orders):
            raise CapitalComError(200, "workingorders")
        return orders

    def get_activity(self, start: datetime, end: datetime) -> list[Mapping[str, Any]]:
        """Return account activity for a bounded, timezone-aware interval."""
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("activity timestamps must be timezone-aware")
        start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
        if start_utc >= end_utc or end_utc - start_utc > timedelta(days=1):
            raise ValueError("activity interval must be positive and at most one day")
        payload = self._request_trading(
            "GET",
            "history/activity",
            params={"from": self._timestamp(start_utc), "to": self._timestamp(end_utc)},
            history=True,
        )
        activities = payload.get("activities") if isinstance(payload, Mapping) else None
        if not isinstance(activities, list) or any(
            not isinstance(item, Mapping) for item in activities
        ):
            raise CapitalComError(200, "history/activity")
        return activities

    @staticmethod
    def _valid_stop_value(value: object) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, int | float)
            and math.isfinite(value)
            and value > 0
        )

    def open_position(self, payload: Mapping[str, object]) -> Mapping[str, Any]:
        """Open only with an explicit non-guaranteed server-side stop attached."""
        stop_level = payload.get("stopLevel")
        stop_distance = payload.get("stopDistance")
        if payload.get("guaranteedStop") is not False or not (
            self._valid_stop_value(stop_level) or self._valid_stop_value(stop_distance)
        ):
            raise ValueError("position requests require guaranteedStop=false and a positive stop")
        reply = self._request_trading("POST", "positions", json=payload)
        if not isinstance(reply, Mapping) or not isinstance(reply.get("dealReference"), str):
            raise CapitalComError(200, "positions")
        if not reply["dealReference"].strip():
            raise CapitalComError(200, "positions")
        return reply

    def confirm_deal(self, deal_reference: str) -> Mapping[str, Any]:
        """Fetch and validate the confirmation object for a trade reference."""
        if not deal_reference.strip():
            raise ValueError("deal_reference must not be empty")
        payload = self._request_trading("GET", f"confirms/{quote(deal_reference, safe='')}")
        if not isinstance(payload, Mapping):
            raise CapitalComError(200, "confirms")
        return payload

    def modify_position(self, deal_id: str, payload: Mapping[str, object]) -> Mapping[str, Any]:
        """Modify stop/limit levels; a protective stop is mandatory."""
        if not deal_id.strip():
            raise ValueError("deal_id must not be empty")
        stop_level = payload.get("stopLevel")
        stop_distance = payload.get("stopDistance")
        if not (self._valid_stop_value(stop_level) or self._valid_stop_value(stop_distance)):
            raise ValueError("position modification requires a positive stop")
        reply = self._request_trading("PUT", f"positions/{quote(deal_id, safe='')}", json=payload)
        if not isinstance(reply, Mapping) or not isinstance(reply.get("dealReference"), str):
            raise CapitalComError(200, "positions")
        if not reply["dealReference"].strip():
            raise CapitalComError(200, "positions")
        return reply

    def close_position(self, deal_id: str) -> Mapping[str, Any]:
        """Request a position close and return its broker deal reference."""
        if not deal_id.strip():
            raise ValueError("deal_id must not be empty")
        reply = self._request_trading("DELETE", f"positions/{quote(deal_id, safe='')}")
        if not isinstance(reply, Mapping) or not isinstance(reply.get("dealReference"), str):
            raise CapitalComError(200, "positions")
        if not reply["dealReference"].strip():
            raise CapitalComError(200, "positions")
        return reply

    def cancel_working_order(self, deal_id: str) -> Mapping[str, Any]:
        """Request cancellation and return its broker deal reference."""
        if not deal_id.strip():
            raise ValueError("deal_id must not be empty")
        reply = self._request_trading("DELETE", f"workingorders/{quote(deal_id, safe='')}")
        if not isinstance(reply, Mapping) or not isinstance(reply.get("dealReference"), str):
            raise CapitalComError(200, "workingorders")
        if not reply["dealReference"].strip():
            raise CapitalComError(200, "workingorders")
        return reply

