"""Collect real safety observations for the demo soak monitor."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

LOGGER = logging.getLogger(__name__)
_SECRET_SHAPED = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|bot\d+:[A-Za-z0-9_-]{20,})")


class LogScanner:
    """Read recent workspace log files for secret-shaped values.

    The matching text is returned only for the current soak evaluation.  It is
    immediately sanitized by ``SoakMonitor`` and is never logged or persisted
    by this scanner.
    """

    def __init__(
        self,
        root: str | Path = "/workspace",
        *,
        secret_patterns: Sequence[str] = (),
        max_files: int = 20,
        max_bytes: int = 65_536,
    ) -> None:
        self._root = Path(root)
        self._patterns = tuple(value for value in secret_patterns if value)
        self._max_files = max(1, max_files)
        self._max_bytes = max(1, max_bytes)

    def soak_observation(self) -> Mapping[str, object]:
        matches: list[str] = []
        try:
            paths = sorted(
                (path for path in self._root.rglob("*.log") if path.is_file()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[: self._max_files]
            for path in paths:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")[-self._max_bytes :]
                except OSError:
                    continue
                if any(secret in text for secret in self._patterns) or _SECRET_SHAPED.search(text):
                    matches.append(text)
        except OSError:
            return {}
        return {"log_text": "\n".join(matches)} if matches else {}


class RuntimeSoakFeed:
    """Merge observations exposed by already-running safety components."""

    def __init__(self, sources: Mapping[str, object]) -> None:
        self._sources = dict(sources)
        self._warned: set[str] = set()

    def collect(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, source in self._sources.items():
            if source is None:
                self._warn(name)
                continue
            provider = getattr(source, "soak_observation", None)
            if not callable(provider):
                self._warn(name)
                continue
            try:
                value = provider()
            except Exception:
                self._warn(name)
                continue
            if isinstance(value, Mapping):
                result.update({str(key): item for key, item in value.items()})
        return result

    def _warn(self, name: str) -> None:
        if name not in self._warned:
            LOGGER.warning("soak observation source unavailable: %s", name)
            self._warned.add(name)


__all__ = ["LogScanner", "RuntimeSoakFeed"]
