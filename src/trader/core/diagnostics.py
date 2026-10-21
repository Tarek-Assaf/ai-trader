"""Bounded, secret-safe diagnostics for runtime failure observations."""

from __future__ import annotations

import re
from typing import Any

_MAX_EXCEPTION_DEPTH = 8
_MAX_EXCEPTION_TEXT = 160
_TOKEN_PATTERN = re.compile(
    r"(?i)(cst|security[-_ ]?token|api[-_ ]?key|password|secret)(\s*[:=]\s*)\S+"
)


def bounded_text(value: object, *, limit: int = _MAX_EXCEPTION_TEXT) -> str:
    """Return a single-line diagnostic value with common credentials redacted."""
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = _TOKEN_PATTERN.sub(r"\1\2[REDACTED]", text)
    return text[:limit]


def exception_chain(exc: BaseException, *, max_depth: int = _MAX_EXCEPTION_DEPTH) -> str:
    """Render an exception and its cause/context chain without traceback payloads."""
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    depth = 0
    relation = "root"
    while current is not None and id(current) not in seen and depth < max_depth:
        seen.add(id(current))
        errno: Any = getattr(current, "errno", None)
        errno_text = bounded_text(errno, limit=40) if errno is not None else "none"
        parts.append(
            f"{relation}:{type(current).__name__}(message={bounded_text(current)},"
            f"errno={errno_text})"
        )
        if current.__cause__ is not None:
            relation = "cause"
            current = current.__cause__
        else:
            relation = "context"
            current = current.__context__
        depth += 1
    if current is not None and id(current) not in seen:
        parts.append("<truncated>")
    return " -> ".join(parts)


__all__ = ["bounded_text", "exception_chain"]
