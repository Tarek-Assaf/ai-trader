"""Read-only Capital.com REST client for Phase 1 market data."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from trader.core.settings import EnvironmentSettings
from trader.integrations.capital_com.rate_limit import RateLimiter, limiter_from_settings

_LOG = logging.getLogger(__name__)
_BASE_URLS = {
    "demo": "https://demo-api-capital.backend-capital.com/api/v1/",
    "live": "https://api-capital.backend-capital.com/api/v1/",
}
_RESOLUTIONS = frozenset(
    {"MINUTE", "MINUTE_5", "MINUTE_15", "MINUTE_30", "HOUR", "HOUR_4", "DAY", "WEEK"}
)


class CapitalComError(RuntimeError):
    """Sanitized API error; deliberately excludes response bodies and headers."""

    def __init__(self, status_code: int | None, path: str) -> None:
        self.status_code = status_code
        self.path = path
        status = f"HTTP {status_code}" if status_code is not None else "transport error"
        super().__init__(f"Capital.com {status} for {path}")


class Resolution(StrEnum):
    """Documented native Capital.com historical resolutions."""

    MINUTE = "MINUTE"
    MINUTE_5 = "MINUTE_5"
    MINUTE_15 = "MINUTE_15"
    MINUTE_30 = "MINUTE_30"
    HOUR = "HOUR"
    HOUR_4 = "HOUR_4"
    DAY = "DAY"
    WEEK = "WEEK"


class CapitalComClient:
    """Synchronous, read-only REST API client.

    The client does not implement order endpoints. Credentials are accepted only
    as SecretStr values from EnvironmentSettings and never included in errors.
    """

    def __init__(
        self,
        settings: EnvironmentSettings,
        *,
        limiter: RateLimiter | None = None,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        api_key = self._secret(settings.capital_api_key, "CAPITAL_API_KEY")
        identifier = self._secret(settings.capital_identifier, "CAPITAL_IDENTIFIER")
        password = self._secret(settings.capital_api_password, "CAPITAL_API_PASSWORD")
        self._api_key = api_key
        self._identifier = identifier
        self._password = password
        self._base_url = _BASE_URLS[settings.broker_env]
        self._limiter = limiter
        if self._limiter is None:
            redis_url = (
                settings.redis_url.get_secret_value() if settings.redis_url is not None else None
            )
            self._limiter = limiter_from_settings(
                redis_url, api_key, embedded_redis=settings.role == "all"
            )
        self._http = http_client or httpx.Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        self._owns_http = http_client is None
        self._sleep = sleep
        self._session_lock = threading.RLock()
        self._cst: str | None = None
        self._security_token: str | None = None

    @staticmethod
    def _secret(value: SecretStr | None, name: str) -> str:
        if value is None or not value.get_secret_value():
            raise ValueError(f"{name} must be configured in the environment")
        return value.get_secret_value()

    @property
    def environment(self) -> str:
        """Return the selected environment name without revealing credentials."""
        return "demo" if "demo-api-capital" in self._base_url else "live"

    def close(self) -> None:
        """Close the underlying HTTP connection pool when owned by this client."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> CapitalComClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        json: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        session_request: bool = False,
        history: bool = False,
    ) -> httpx.Response:
        assert self._limiter is not None
        for attempt in range(3):
            self._limiter.acquire(session=session_request, history=history)
            try:
                response = self._http.request(
                    method, f"{self._base_url}{path}", params=params, json=json, headers=headers
                )
            except httpx.HTTPError as exc:
                _LOG.warning("Capital.com transport error for %s (%s)", path, type(exc).__name__)
                raise CapitalComError(None, path) from None
            if response.status_code == 401 and not session_request:
                return response
            if response.status_code != 429 or attempt == 2:
                if response.status_code >= 400:
                    _LOG.warning(
                        "Capital.com request failed: %s returned HTTP %s",
                        path,
                        response.status_code,
                    )
                    raise CapitalComError(response.status_code, path)
                return response
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = max(0.1, min(float(retry_after), 30.0))
            except ValueError:
                delay = min(0.5 * (2**attempt), 5.0)
            self._sleep(delay)
        raise AssertionError("unreachable")

    def _open_session(self) -> None:
        with self._session_lock:
            if self._cst and self._security_token:
                return
            response = self._send(
                "POST",
                "session",
                json={"identifier": self._identifier, "password": self._password},
                headers={"X-CAP-API-KEY": self._api_key},
                session_request=True,
            )
            cst = response.headers.get("CST")
            security_token = response.headers.get("X-SECURITY-TOKEN")
            if not cst or not security_token:
                raise CapitalComError(response.status_code, "session")
            self._cst, self._security_token = cst, security_token

    def _auth_headers(self) -> dict[str, str]:
        self._open_session()
        assert self._cst is not None and self._security_token is not None
        return {"CST": self._cst, "X-SECURITY-TOKEN": self._security_token}

    def _request(
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

    def ping(self) -> Mapping[str, Any]:
        """Ping the active session and return the server response."""
        payload = self._request("GET", "ping")
        if not isinstance(payload, Mapping):
            raise CapitalComError(200, "ping")
        return payload

    def search_markets(self, search_term: str) -> list[Mapping[str, Any]]:
        """Search market names/symbols and return matching market objects."""
        term = search_term.strip()
        if not term:
            raise ValueError("search_term must not be empty")
        payload = self._request("GET", "markets", params={"searchTerm": term})
        markets = payload.get("markets") if isinstance(payload, Mapping) else None
        if not isinstance(markets, list):
            raise CapitalComError(200, "markets")
        return markets

    def market_details(self, epic: str) -> Mapping[str, Any]:
        """Fetch dealing rules and current market status for an epic."""
        payload = self._request("GET", f"markets/{quote(epic, safe='')}")
        if not isinstance(payload, Mapping):
            raise CapitalComError(200, "markets")
        return payload

    def account_info(self) -> Mapping[str, Any]:
        """Return the current account list; callers should avoid persisting PII."""
        payload = self._request("GET", "accounts")
        if not isinstance(payload, Mapping):
            raise CapitalComError(200, "accounts")
        return payload

    def get_positions(self) -> list[Mapping[str, Any]]:
        """Return validated open-position records."""
        payload = self._request("GET", "positions")
        positions = payload.get("positions") if isinstance(payload, Mapping) else None
        if not isinstance(positions, list) or any(not isinstance(item, Mapping) for item in positions):
            raise CapitalComError(200, "positions")
        return positions

    def get_working_orders(self) -> list[Mapping[str, Any]]:
        """Return validated working-order records."""
        payload = self._request("GET", "workingorders")
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
        payload = self._request(
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

    def open_position(self, payload: Mapping[str, object]) -> Mapping[str, Any]:
        """Open only with an explicit non-guaranteed server-side stop attached."""
        stop_level = payload.get("stopLevel")
        stop_distance = payload.get("stopDistance")
        if payload.get("guaranteedStop") is not False or not (
            isinstance(stop_level, int | float) and stop_level > 0
            or isinstance(stop_distance, int | float) and stop_distance > 0
        ):
            raise ValueError("position requests require guaranteedStop=false and a positive stop")
        reply = self._request("POST", "positions", json=payload)
        if not isinstance(reply, Mapping) or not isinstance(reply.get("dealReference"), str):
            raise CapitalComError(200, "positions")
        if not reply["dealReference"].strip():
            raise CapitalComError(200, "positions")
        return reply

    def confirm_deal(self, deal_reference: str) -> Mapping[str, Any]:
        """Fetch and validate the confirmation object for a trade reference."""
        if not deal_reference.strip():
            raise ValueError("deal_reference must not be empty")
        payload = self._request("GET", f"confirms/{quote(deal_reference, safe='')}")
        if not isinstance(payload, Mapping):
            raise CapitalComError(200, "confirms")
        return payload

    def modify_position(
        self, deal_id: str, payload: Mapping[str, object]
    ) -> Mapping[str, Any]:
        """Modify stop/limit levels; a protective stop is mandatory."""
        if not deal_id.strip():
            raise ValueError("deal_id must not be empty")
        stop_level = payload.get("stopLevel")
        stop_distance = payload.get("stopDistance")
        if not (
            isinstance(stop_level, int | float) and stop_level > 0
            or isinstance(stop_distance, int | float) and stop_distance > 0
        ):
            raise ValueError("position modification requires a positive stop")
        reply = self._request(
            "PUT", f"positions/{quote(deal_id, safe='')}", json=payload
        )
        if not isinstance(reply, Mapping) or not isinstance(reply.get("dealReference"), str):
            raise CapitalComError(200, "positions")
        if not reply["dealReference"].strip():
            raise CapitalComError(200, "positions")
        return reply

    def close_position(self, deal_id: str) -> Mapping[str, Any]:
        """Request a position close and return its broker deal reference."""
        if not deal_id.strip():
            raise ValueError("deal_id must not be empty")
        reply = self._request("DELETE", f"positions/{quote(deal_id, safe='')}")
        if not isinstance(reply, Mapping) or not isinstance(reply.get("dealReference"), str):
            raise CapitalComError(200, "positions")
        if not reply["dealReference"].strip():
            raise CapitalComError(200, "positions")
        return reply

    def cancel_working_order(self, deal_id: str) -> Mapping[str, Any]:
        """Request cancellation and return its broker deal reference."""
        if not deal_id.strip():
            raise ValueError("deal_id must not be empty")
        reply = self._request("DELETE", f"workingorders/{quote(deal_id, safe='')}")
        if not isinstance(reply, Mapping) or not isinstance(reply.get("dealReference"), str):
            raise CapitalComError(200, "workingorders")
        if not reply["dealReference"].strip():
            raise CapitalComError(200, "workingorders")
        return reply

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None:
            raise ValueError("price history timestamps must be timezone-aware")
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")

    def iter_price_history(
        self,
        epic: str,
        resolution: Resolution | str,
        start: datetime,
        end: datetime,
        *,
        page_size: int = 1000,
        max_pages: int = 100,
    ) -> Iterator[Mapping[str, Any]]:
        """Yield historical candles, paging forward in bounded 1000-row requests."""
        resolution_value = str(resolution)
        if resolution_value not in _RESOLUTIONS:
            raise ValueError(f"unsupported Capital.com resolution: {resolution_value}")
        if page_size < 1 or page_size > 1000:
            raise ValueError("page_size must be between 1 and 1000")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        cursor = start
        end_utc = end.astimezone(UTC) if end.tzinfo else None
        if end_utc is None or start.tzinfo is None:
            raise ValueError("price history timestamps must be timezone-aware")
        if cursor.astimezone(UTC) > end_utc:
            raise ValueError("start must not be after end")
        seen: set[str] = set()
        path = f"prices/{quote(epic, safe='')}"
        for _ in range(max_pages):
            payload = self._request(
                "GET",
                path,
                params={
                    "resolution": resolution_value,
                    "from": self._timestamp(cursor),
                    "to": self._timestamp(end_utc),
                    "max": page_size,
                },
                history=True,
            )
            prices = payload.get("prices") if isinstance(payload, Mapping) else None
            if not isinstance(prices, list):
                raise CapitalComError(200, path)
            if not prices:
                return
            last_stamp: datetime | None = None
            for candle in prices:
                if not isinstance(candle, Mapping):
                    raise CapitalComError(200, path)
                stamp_text = candle.get("snapshotTimeUTC") or candle.get("snapshotTime")
                if not isinstance(stamp_text, str):
                    raise CapitalComError(200, path)
                try:
                    stamp = datetime.fromisoformat(stamp_text.replace("Z", "+00:00"))
                except ValueError:
                    raise CapitalComError(200, path) from None
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                stamp = stamp.astimezone(UTC)
                last_stamp = stamp if last_stamp is None or stamp > last_stamp else last_stamp
                key = stamp.isoformat()
                if key not in seen:
                    seen.add(key)
                    yield candle
            if len(prices) < page_size:
                return
            if last_stamp is None:
                return
            next_cursor = last_stamp + timedelta(seconds=1)
            if next_cursor <= cursor.astimezone(UTC):
                raise CapitalComError(200, path)
            cursor = next_cursor
            if cursor > end_utc:
                return
        raise CapitalComError(200, path)
