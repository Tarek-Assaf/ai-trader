"""Normalize fetched research as bounded, provenance-bearing untrusted data."""

from __future__ import annotations

import hashlib
import html
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_MAX_CONTENT_CHARS = 20_000
_SENSITIVE_QUERY_KEYS = ("api_key", "token", "secret", "password", "authorization", "key")


def redact_url(url: str) -> str:
    """Remove credential-like query values before a URL is persisted as provenance."""
    parts = urlsplit(url)
    query = [
        (key, "[REDACTED]" if any(term in key.casefold() for term in _SENSITIVE_QUERY_KEYS) else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class _TextExtractor(HTMLParser):
    """Extract text while discarding script/style/template content."""

    _DROP = frozenset({"script", "style", "template", "noscript", "svg"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() in self._DROP:
            self._drop_depth += 1
        elif self._drop_depth == 0 and tag.casefold() in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self._DROP and self._drop_depth:
            self._drop_depth -= 1
        elif self._drop_depth == 0 and tag.casefold() in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._drop_depth == 0:
            self.parts.append(data)


def strip_markup(value: str) -> str:
    """Extract visible text and remove control and Unicode format characters."""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = html.unescape("".join(parser.parts))
    cleaned = "".join(
        char
        for char in text
        if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
    )
    return "\n".join(line.strip() for line in cleaned.splitlines() if line.strip())


@dataclass(frozen=True, slots=True)
class ResearchItem:
    """Sanitized source content and the provenance needed to audit it."""

    id: str
    source: str
    url: str
    fetched_at: datetime
    content: str
    content_hash: str

    def __post_init__(self) -> None:
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if not self.content_hash or len(self.content_hash) != 64:
            raise ValueError("content_hash must be a SHA-256 hex digest")

    @property
    def untrusted_block(self) -> str:
        """Return a bounded wrapper whose attributes and body cannot close the wrapper."""
        source = html.escape(self.source, quote=True)
        fetched = self.fetched_at.astimezone(UTC).isoformat()
        return (
            f'<untrusted_content source="{source}" fetched_at="{fetched}" '
            f'provenance_id="{html.escape(self.id, quote=True)}">'
            f"{self.content}</untrusted_content>"
        )


def sanitize_research(
    *,
    item_id: str,
    source: str,
    url: str,
    fetched_at: datetime,
    content: str,
    max_chars: int = _MAX_CONTENT_CHARS,
) -> ResearchItem:
    """Strip active markup and wrap escaped content as untrusted data."""
    if not item_id or not source.strip():
        raise ValueError("item_id and source are required")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    redacted_url = redact_url(url)
    parts = urlsplit(redacted_url)
    if (
        parts.scheme.casefold() != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError("provenance URL must use HTTPS without embedded credentials")
    plain = strip_markup(content)[:max_chars]
    escaped = html.escape(plain, quote=False)
    digest = hashlib.sha256(escaped.encode("utf-8")).hexdigest()
    return ResearchItem(
        id=item_id,
        source=source.strip(),
        url=redacted_url,
        fetched_at=fetched_at.astimezone(UTC),
        content=escaped,
        content_hash=digest,
    )


def supported_claims(
    claims: list[tuple[str, tuple[str, ...]]], provenance_ids: set[str]
) -> tuple[str, ...]:
    """Keep claims only when they cite at least one stored provenance record."""
    return tuple(
        text
        for text, citations in claims
        if text.strip() and any(citation in provenance_ids for citation in citations)
    )
