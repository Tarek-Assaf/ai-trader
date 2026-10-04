"""Bounded, cached HTTP source adapters with provenance persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import socket
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from trader.core.db import metadata, tables
from trader.research.cache import SQLiteResearchCache, encode_cache_value
from trader.research.sanitizer import ResearchItem, sanitize_research


class SourceError(RuntimeError):
    """Safe-to-report source failure that contains no request credentials or response body."""


class InvalidSourceResponse(SourceError):
    """Remote HTTP response is successful but its shape or size is unsafe."""


@dataclass(slots=True)
class Reliability:
    """Laplace-smoothed source success ratio, updated only after validated responses."""

    successes: int = 0
    failures: int = 0

    @property
    def score(self) -> float:
        return (self.successes + 1) / (self.successes + self.failures + 2)

    def record(self, succeeded: bool) -> None:
        if succeeded:
            self.successes += 1
        else:
            self.failures += 1


class ResearchRepository:
    """Persist source provenance through the existing research_items database table."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        metadata.create_all(engine, tables=[tables["research_items"]])

    def save(self, item: ResearchItem) -> None:
        """Store sanitized content and its reproducibility metadata."""
        payload = {
            "source": item.source,
            "url": item.url,
            "fetched_at": item.fetched_at.isoformat(),
            "content": item.content,
            "content_hash": item.content_hash,
        }
        table = tables["research_items"]
        values = {
            "id": item.id,
            "created_at": item.fetched_at,
            "cycle_id": None,
            "payload": payload,
        }
        dialect = self._engine.dialect.name
        if dialect == "sqlite":
            statement = sqlite_insert(table).values(**values).on_conflict_do_nothing(
                index_elements=["id"]
            )
        elif dialect == "postgresql":
            statement = pg_insert(table).values(**values).on_conflict_do_nothing(
                index_elements=["id"]
            )
        else:
            raise ValueError("research provenance supports PostgreSQL or SQLite")
        with self._engine.begin() as connection:
            connection.execute(statement)


class HttpResearchSource(ABC):
    """Shared network, cache, rate-limit, retry, size, and provenance behavior."""

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        allowed_hosts: set[str],
        cache: SQLiteResearchCache,
        repository: ResearchRepository,
        ttl_seconds: float,
        min_interval_seconds: float,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 1_000_000,
        attempts: int = 2,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        backoff_seconds: float = 0.25,
        allowed_media_types: set[str] | frozenset[str] = frozenset(
            {"application/json", "application/rss+xml", "application/atom+xml", "application/xml", "text/xml", "text/html", "text/plain"}
        ),
        resolve_dns: Callable[[str], bool] | None = None,
    ) -> None:
        if not name.strip() or ttl_seconds <= 0 or min_interval_seconds < 0:
            raise ValueError("source name, positive TTL, and non-negative rate interval required")
        if timeout_seconds <= 0 or max_response_bytes < 1 or attempts < 1 or backoff_seconds < 0:
            raise ValueError("invalid HTTP source limits")
        self.name = name.strip()
        self._resolve_dns = resolve_dns or resolve_public_host
        self.endpoint = self._validate_url(endpoint, allowed_hosts, self._resolve_dns)
        self.allowed_hosts = {host.casefold() for host in allowed_hosts}
        self.cache = cache
        self.repository = repository
        self.ttl_seconds = ttl_seconds
        self.min_interval_seconds = min_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.attempts = attempts
        self.api_key = api_key
        self.client = client
        self.backoff_seconds = backoff_seconds
        self.allowed_media_types = frozenset(value.casefold() for value in allowed_media_types)
        self.reliability = Reliability()

    @staticmethod
    def _validate_url(
        url: str, allowed_hosts: set[str], resolve_dns: Callable[[str], bool]
    ) -> str:
        parts = urlsplit(url)
        host = (parts.hostname or "").casefold()
        if (
            parts.scheme.casefold() != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or host not in {entry.casefold() for entry in allowed_hosts}
            or parts.port not in {None, 443}
        ):
            raise ValueError("source URL must use HTTPS and a configured host")
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("source URL cannot target a private or special-use address")
        if address is None and not resolve_dns(host):
            raise ValueError("source host does not resolve exclusively to public addresses")
        return url

    @abstractmethod
    def request_spec(
        self, query: Mapping[str, str]
    ) -> tuple[str, Mapping[str, str], Mapping[str, str]]:
        """Return URL, query parameters, and headers for a normalized query."""

    @abstractmethod
    def parse_response(self, body: bytes, request_url: str) -> list[tuple[str, str]]:
        """Validate a response and return provenance URL/content pairs."""

    async def fetch(self, query: Mapping[str, str]) -> tuple[ResearchItem, ...]:
        """Fetch a query once per TTL, then persist each validated sanitized item."""
        normalized = {str(key): str(value) for key, value in sorted(query.items())}
        cache_material = json.dumps(
            {"source": self.name, "query": normalized}, sort_keys=True, separators=(",", ":")
        )
        cache_key = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()
        cached = self.cache.get(cache_key)
        if cached is not None:
            return self._decode_items(cached)

        url, params, headers = self.request_spec(normalized)
        self._validate_url(url, self.allowed_hosts, self._resolve_dns)
        try:
            body, fetched_at = await self._request(url, params, headers)
            records = self.parse_response(body, url)
            if not records:
                raise InvalidSourceResponse("source returned no usable records")
            items = tuple(
                sanitize_research(
                    item_id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{cache_key}:{index}:{record_url}:{text}",
                        )
                    ),
                    source=self.name,
                    url=record_url,
                    fetched_at=fetched_at,
                    content=text,
                )
                for index, (record_url, text) in enumerate(records)
            )
            for item in items:
                self.repository.save(item)
            payload = encode_cache_value(
                {
                    "items": [
                        {
                            "id": item.id,
                            "source": item.source,
                            "url": item.url,
                            "fetched_at": item.fetched_at.isoformat(),
                            "content": item.content,
                            "content_hash": item.content_hash,
                        }
                        for item in items
                    ]
                }
            )
            self.cache.set(cache_key, payload, self.ttl_seconds)
        except SourceError:
            self.reliability.record(False)
            raise
        except Exception:
            # Do not propagate driver, parser, HTTP or database exception text; it can include
            # credential-bearing URLs or provider response bodies.
            self.reliability.record(False)
            raise SourceError(f"{self.name} source failed") from None
        self.reliability.record(True)
        return items

    async def _request(
        self, url: str, params: Mapping[str, str], headers: Mapping[str, str]
    ) -> tuple[bytes, datetime]:
        last_error: SourceError | None = None
        for attempt in range(self.attempts):
            wait = self.cache.reserve_request(self.name, self.min_interval_seconds)
            if wait:
                await asyncio.sleep(wait)
            try:
                body = await self._get_bounded(url, params, headers)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status < 500 and status != 429:
                    raise SourceError(f"{self.name} returned HTTP {status}") from None
                last_error = SourceError(f"{self.name} temporarily unavailable")
            except httpx.RequestError:
                last_error = SourceError(f"{self.name} request failed")
            except InvalidSourceResponse:
                raise
            else:
                return body, datetime.now(UTC)
            if attempt + 1 < self.attempts and self.backoff_seconds:
                await asyncio.sleep(self.backoff_seconds * (2**attempt))
        raise last_error or SourceError(f"{self.name} request failed")

    async def _get_bounded(
        self, url: str, params: Mapping[str, str], headers: Mapping[str, str]
    ) -> bytes:
        if self.client is not None:
            return await self._stream(self.client, url, params, headers)
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, follow_redirects=False, trust_env=False
        ) as client:
            return await self._stream(client, url, params, headers)

    async def _stream(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> bytes:
        async with client.stream(
            "GET",
            url,
            params=params,
            headers=headers,
            timeout=self.timeout_seconds,
            follow_redirects=False,
        ) as response:
            if 300 <= response.status_code < 400:
                raise InvalidSourceResponse("source redirects are not followed")
            response.raise_for_status()
            media_type = response.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().casefold()
            if media_type not in self.allowed_media_types:
                raise InvalidSourceResponse("source returned an unsupported content type")
            declared = response.headers.get("content-length")
            if declared is not None and int(declared) > self.max_response_bytes:
                raise InvalidSourceResponse("source response exceeds size limit")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > self.max_response_bytes:
                    raise InvalidSourceResponse("source response exceeds size limit")
            return bytes(body)

    def _decode_items(self, value: str) -> tuple[ResearchItem, ...]:
        try:
            payload = json.loads(value)
            items = payload["items"]
            if not isinstance(items, list):
                raise ValueError
            decoded = []
            for item in items:
                fetched = datetime.fromisoformat(item["fetched_at"])
                decoded.append(
                    ResearchItem(
                        id=item["id"],
                        source=item["source"],
                        url=item["url"],
                        fetched_at=fetched,
                        content=item["content"],
                        content_hash=item["content_hash"],
                    )
                )
            return tuple(decoded)
        except (KeyError, TypeError, ValueError):
            raise SourceError(f"{self.name} cache entry is invalid") from None


def require_json_object(body: bytes) -> dict[str, Any]:
    """Decode UTF-8 JSON and require a top-level object."""
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidSourceResponse("source did not return valid UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise InvalidSourceResponse("source JSON response has an invalid shape")
    return value


def as_records(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    """Extract a validated list of object records under a configured response key."""
    records = payload.get(key)
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise InvalidSourceResponse("source JSON response has an invalid record list")
    return records


def redacted_endpoint_host(url: str) -> str:
    """Return only a validated host for non-sensitive diagnostics."""
    return urlsplit(url).hostname or ""


def finite_json(value: object) -> bool:
    """Reject NaN and infinities before treating provider JSON values as data."""
    if isinstance(value, float):
        return math.isfinite(value)
    return True


def resolve_public_host(host: str) -> bool:
    """Require DNS answers to be public; network failures fail closed."""
    try:
        addresses = {ip_address(result[4][0]) for result in socket.getaddrinfo(host, None)}
    except (OSError, ValueError):
        return False
    return bool(addresses) and all(address.is_global for address in addresses)
