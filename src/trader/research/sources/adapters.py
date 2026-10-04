"""Configurable web, RSS, FRED, economic-calendar and COT adapters."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from trader.research.cache import SQLiteResearchCache
from trader.research.sources.base import (
    HttpResearchSource,
    InvalidSourceResponse,
    ResearchRepository,
    as_records,
    require_json_object,
)


def _https_url(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidSourceResponse("source record has no URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise InvalidSourceResponse("source record URL must use HTTPS")
    return value


def _authorization(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


class URLFetchSource(HttpResearchSource):
    """Fetch configured public HTTPS hosts only; redirects and private targets are rejected."""

    def __init__(
        self,
        *,
        allowed_hosts: set[str],
        cache: SQLiteResearchCache,
        repository: ResearchRepository,
        ttl_seconds: float = 300,
        min_interval_seconds: float = 1,
        **kwargs: Any,
    ) -> None:
        if not allowed_hosts:
            raise ValueError("URL fetch requires an explicit host allow-list")
        first_host = sorted(allowed_hosts)[0]
        super().__init__(
            name="url_fetch",
            endpoint=f"https://{first_host}/",
            allowed_hosts=allowed_hosts,
            cache=cache,
            repository=repository,
            ttl_seconds=ttl_seconds,
            min_interval_seconds=min_interval_seconds,
            allowed_media_types={"text/html", "text/plain"},
            **kwargs,
        )

    def request_spec(
        self, query: Mapping[str, str]
    ) -> tuple[str, Mapping[str, str], Mapping[str, str]]:
        url = query.get("url", "")
        return url, {}, {"Accept": "text/html, text/plain;q=0.9"}

    def parse_response(self, body: bytes, request_url: str) -> list[tuple[str, str]]:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidSourceResponse("URL response must be UTF-8 text") from None
        return [(request_url, text)]


class RSSSource(HttpResearchSource):
    """Parse bounded RSS or Atom items without enabling external XML entities."""

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        allowed_hosts: set[str],
        cache: SQLiteResearchCache,
        repository: ResearchRepository,
        ttl_seconds: float = 300,
        min_interval_seconds: float = 1,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name=name,
            endpoint=endpoint,
            allowed_hosts=allowed_hosts,
            cache=cache,
            repository=repository,
            ttl_seconds=ttl_seconds,
            min_interval_seconds=min_interval_seconds,
            allowed_media_types={
                "application/rss+xml",
                "application/atom+xml",
                "application/xml",
                "text/xml",
            },
            **kwargs,
        )

    def request_spec(
        self, query: Mapping[str, str]
    ) -> tuple[str, Mapping[str, str], Mapping[str, str]]:
        del query
        return self.endpoint, {}, {"Accept": "application/rss+xml, application/atom+xml, text/xml"}

    def parse_response(self, body: bytes, request_url: str) -> list[tuple[str, str]]:
        del request_url
        upper = body.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise InvalidSourceResponse("RSS response contains a forbidden declaration")
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            raise InvalidSourceResponse("RSS response is malformed") from None
        records: list[tuple[str, str]] = []
        for entry in root.iter():
            kind = entry.tag.rsplit("}", 1)[-1].casefold()
            if kind not in {"item", "entry"}:
                continue
            fields: dict[str, str] = {}
            for child in entry:
                name = child.tag.rsplit("}", 1)[-1].casefold()
                value = child.attrib.get("href", "") or "".join(child.itertext())
                if name in {"title", "description", "summary", "content", "link"} and value:
                    fields[name] = value.strip()
            link = fields.get("link", "")
            if not link:
                continue
            title = fields.get("title", "")
            description = fields.get(
                "description", fields.get("summary", fields.get("content", ""))
            )
            text = "\n".join(value for value in (title, description) if value)
            if text:
                records.append((_https_url(link), text))
            if len(records) >= 100:
                break
        return records


class JsonRecordsSource(HttpResearchSource):
    """Validate a JSON record list and expose only bounded scalar fields as source data."""

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        allowed_hosts: set[str],
        records_key: str,
        fields: tuple[str, ...],
        cache: SQLiteResearchCache,
        repository: ResearchRepository,
        ttl_seconds: float,
        min_interval_seconds: float,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.records_key = records_key
        self.fields = fields
        super().__init__(
            name=name,
            endpoint=endpoint,
            allowed_hosts=allowed_hosts,
            cache=cache,
            repository=repository,
            ttl_seconds=ttl_seconds,
            min_interval_seconds=min_interval_seconds,
            api_key=api_key,
            allowed_media_types={"application/json"},
            **kwargs,
        )

    def request_spec(
        self, query: Mapping[str, str]
    ) -> tuple[str, Mapping[str, str], Mapping[str, str]]:
        return self.endpoint, query, _authorization(self.api_key)

    def parse_response(self, body: bytes, request_url: str) -> list[tuple[str, str]]:
        payload = require_json_object(body)
        rows = as_records(payload, self.records_key)
        results: list[tuple[str, str]] = []
        for row in rows[:100]:
            url = _https_url(row.get("url", request_url))
            selected = {
                field: value
                for field in self.fields
                if (value := row.get(field)) is not None
                and isinstance(value, str | int | float | bool)
            }
            if not selected:
                raise InvalidSourceResponse("source record has no configured scalar fields")
            try:
                text = json.dumps(selected, sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError):
                raise InvalidSourceResponse("source record contains invalid values") from None
            results.append((url, text))
        return results


class WebSearchSource(HttpResearchSource):
    """Adapter for a configured web-search API returning a results array."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_hosts: set[str],
        cache: SQLiteResearchCache,
        repository: ResearchRepository,
        api_key: str,
        ttl_seconds: float = 300,
        min_interval_seconds: float = 1,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="web_search",
            endpoint=endpoint,
            allowed_hosts=allowed_hosts,
            cache=cache,
            repository=repository,
            ttl_seconds=ttl_seconds,
            min_interval_seconds=min_interval_seconds,
            api_key=api_key,
            allowed_media_types={"application/json"},
            **kwargs,
        )

    def request_spec(
        self, query: Mapping[str, str]
    ) -> tuple[str, Mapping[str, str], Mapping[str, str]]:
        term = query.get("query", "").strip()
        if not term:
            raise ValueError("web search query is required")
        return self.endpoint, {"q": term}, _authorization(self.api_key)

    def parse_response(self, body: bytes, request_url: str) -> list[tuple[str, str]]:
        del request_url
        rows = as_records(require_json_object(body), "results")
        results = []
        for row in rows[:100]:
            url = _https_url(row.get("url"))
            title, snippet = row.get("title"), row.get("snippet")
            if not isinstance(title, str) or not isinstance(snippet, str):
                raise InvalidSourceResponse("search result is missing title or snippet")
            results.append((url, f"{title}\n{snippet}"))
        return results


class FredSource(JsonRecordsSource):
    """FRED series observations; the API key is sent only in the HTTPS request query."""

    def __init__(
        self,
        *,
        cache: SQLiteResearchCache,
        repository: ResearchRepository,
        api_key: str,
        ttl_seconds: float = 3600,
        min_interval_seconds: float = 1,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="fred",
            endpoint="https://api.stlouisfed.org/fred/series/observations",
            allowed_hosts={"api.stlouisfed.org"},
            records_key="observations",
            fields=("date", "value", "realtime_start", "realtime_end"),
            cache=cache,
            repository=repository,
            ttl_seconds=ttl_seconds,
            min_interval_seconds=min_interval_seconds,
            api_key=api_key,
            **kwargs,
        )

    def request_spec(
        self, query: Mapping[str, str]
    ) -> tuple[str, Mapping[str, str], Mapping[str, str]]:
        series_id = query.get("series_id", "").strip()
        if not series_id or not self.api_key:
            raise ValueError("FRED series_id and API key are required")
        return (
            self.endpoint,
            {"series_id": series_id, "api_key": self.api_key, "file_type": "json"},
            {},
        )


class EconomicCalendarSource(JsonRecordsSource):
    """Configured calendar API adapter with explicit endpoint and response schema."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_hosts: set[str],
        cache: SQLiteResearchCache,
        repository: ResearchRepository,
        api_key: str | None = None,
        ttl_seconds: float = 300,
        min_interval_seconds: float = 1,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="economic_calendar",
            endpoint=endpoint,
            allowed_hosts=allowed_hosts,
            records_key="events",
            fields=(
                "date",
                "time",
                "currency",
                "country",
                "title",
                "impact",
                "actual",
                "forecast",
                "previous",
            ),
            cache=cache,
            repository=repository,
            ttl_seconds=ttl_seconds,
            min_interval_seconds=min_interval_seconds,
            api_key=api_key,
            **kwargs,
        )


class CotSource(JsonRecordsSource):
    """Configured COT data API adapter with explicit endpoint and response schema."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_hosts: set[str],
        cache: SQLiteResearchCache,
        repository: ResearchRepository,
        api_key: str | None = None,
        ttl_seconds: float = 86_400,
        min_interval_seconds: float = 1,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="cot",
            endpoint=endpoint,
            allowed_hosts=allowed_hosts,
            records_key="data",
            fields=("date", "market", "contract", "open_interest", "long", "short", "report_type"),
            cache=cache,
            repository=repository,
            ttl_seconds=ttl_seconds,
            min_interval_seconds=min_interval_seconds,
            api_key=api_key,
            **kwargs,
        )
