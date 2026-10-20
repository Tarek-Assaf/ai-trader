"""Read-only Capital.com REST client for Phase 1 market data."""

from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
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
_MAX_429_ATTEMPTS = 4
_KEEPALIVE_SECONDS = 300.0
_SHARED_SESSION_LOCK = threading.RLock()
_SHARED_SESSIONS: dict[tuple[str, str, int], dict[str, object]] = {}


class CapitalComError(RuntimeError):
    """Sanitized API error; deliberately excludes response bodies and headers."""

    def __init__(
        self,
        status_code: int | None,
        path: str,
        *,
        error_code: str | None = None,
        attempt: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.path = path
        self.error_code = error_code
        self.attempt = attempt
        self.retry_after = retry_after
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
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        api_key = self._secret(settings.capital_api_key, "CAPITAL_API_KEY")
        identifier = self._secret(settings.capital_identifier, "CAPITAL_IDENTIFIER")
        password = self._secret(settings.capital_api_password, "CAPITAL_API_PASSWORD")
        self._api_key = api_key
        self._identifier = identifier
        self._password = password
        self._base_url = _BASE_URLS[settings.broker_env]
        credential_digest = hashlib.sha256(f"{api_key}\0{identifier}".encode()).hexdigest()
        transport_scope = id(http_client) if http_client is not None else 0
        self._session_cache_key = (self._base_url, credential_digest, transport_scope)
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
        self._jitter = jitter or (lambda base: random.uniform(0.0, base * 0.25))
        self._session_lock = threading.RLock()
        self._cst: str | None = None
        self._security_token: str | None = None

    def _invalidate_session(self) -> None:
        with self._session_lock:
            old_tokens = (self._cst, self._security_token)
            self._cst = None
            self._security_token = None
            with _SHARED_SESSION_LOCK:
                shared = _SHARED_SESSIONS.get(self._session_cache_key)
                if shared is not None and (shared.get("cst"), shared.get("token")) == old_tokens:
                    _SHARED_SESSIONS.pop(self._session_cache_key, None)

    def _mark_ping(self) -> None:
        with _SHARED_SESSION_LOCK:
            shared = _SHARED_SESSIONS.get(self._session_cache_key)
            if shared is not None:
                shared["last_ping"] = time.monotonic()

    def _keepalive_due(self) -> bool:
        with _SHARED_SESSION_LOCK:
            shared = _SHARED_SESSIONS.get(self._session_cache_key)
            if shared is None:
                return False
            last_ping = shared.get("last_ping")
            return (
                not isinstance(last_ping, float)
                or time.monotonic() - last_ping >= _KEEPALIVE_SECONDS
            )

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
        retry_429: bool = True,
    ) -> httpx.Response:
        assert self._limiter is not None
        for attempt in range(1, _MAX_429_ATTEMPTS + 1):
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
            if response.status_code != 429 or not retry_429 or attempt == _MAX_429_ATTEMPTS:
                if response.status_code >= 400:
                    _LOG.warning(
                        "Capital.com request failed: %s returned HTTP %s",
                        path,
                        response.status_code,
                    )
                    raise CapitalComError(
                        response.status_code,
                        path,
                        error_code=self._response_error_code(response),
                        attempt=attempt,
                        retry_after=response.headers.get("Retry-After"),
                    )
                return response
            delay = self._retry_delay(response, attempt)
            self._sleep(delay)
        raise AssertionError("unreachable")

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After", "").strip()
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = float(parsedate_to_datetime(retry_after).timestamp())
                    return max(0.0, retry_at - time.time())
                except (TypeError, ValueError, OverflowError):
                    pass
        base = float(2 ** (attempt - 1))
        return max(0.0, base + self._jitter(base))

    @staticmethod
    def _response_error_code(response: httpx.Response) -> str | None:
        """Extract only Capital.com's non-secret errorCode diagnostic."""
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, Mapping):
            return None
        error_code = payload.get("errorCode")
        return error_code if isinstance(error_code, str) else None

    def _open_session(self) -> None:
        with self._session_lock:
            if self._cst and self._security_token:
                return
            with _SHARED_SESSION_LOCK:
                shared = _SHARED_SESSIONS.get(self._session_cache_key)
                if shared is not None:
                    shared_cst, shared_token = shared.get("cst"), shared.get("token")
                    if isinstance(shared_cst, str) and isinstance(shared_token, str):
                        self._cst, self._security_token = shared_cst, shared_token
                        return
                response = self._send(
                    "POST",
                    "session",
                    json={"identifier": self._identifier, "password": self._password},
                    headers={"X-CAP-API-KEY": self._api_key},
                    session_request=True,
                )
                response_cst = response.headers.get("CST")
                response_token = response.headers.get("X-SECURITY-TOKEN")
                if not response_cst or not response_token:
                    raise CapitalComError(response.status_code, "session")
                self._cst, self._security_token = response_cst, response_token
                _SHARED_SESSIONS[self._session_cache_key] = {
                    "cst": response_cst,
                    "token": response_token,
                    "last_ping": time.monotonic(),
                }

    def _auth_headers(self) -> dict[str, str]:
        self._open_session()
        assert self._cst is not None and self._security_token is not None
        return {"CST": self._cst, "X-SECURITY-TOKEN": self._security_token}

    def renew_session(self) -> None:
        """Discard the current session and create a fresh bounded session.

        The caller uses this after a transport or keepalive failure.  Session
        requests retain the client's existing rate limiter and 429 backoff;
        no credentials or response bodies are included in diagnostics.
        """
        self._invalidate_session()
        self._open_session()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        history: bool = False,
    ) -> Any:
        if method != "GET":
            raise ValueError("read-only CapitalComClient supports GET requests only")
        for auth_attempt in range(2):
            headers = self._auth_headers()
            if path != "ping" and self._keepalive_due():
                keepalive = self._send("GET", "ping", headers=headers)
                if keepalive.status_code == 401:
                    self._invalidate_session()
                    continue
                self._mark_ping()
            response = self._send(method, path, params=params, headers=headers, history=history)
            if response.status_code != 401:
                try:
                    return response.json()
                except ValueError:
                    raise CapitalComError(response.status_code, path) from None
            self._invalidate_session()
            if auth_attempt == 1:
                raise CapitalComError(
                    401,
                    path,
                    error_code=self._response_error_code(response),
                )
        raise AssertionError("unreachable")

    def ping(self) -> Mapping[str, Any]:
        """Ping the active session and return the server response."""
        payload = self._request("GET", "ping")
        if not isinstance(payload, Mapping):
            raise CapitalComError(200, "ping")
        self._mark_ping()
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
