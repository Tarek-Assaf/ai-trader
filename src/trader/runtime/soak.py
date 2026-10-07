"""Collect real safety observations for the demo soak monitor."""

from __future__ import annotations

import logging
from collections.abc import Mapping

LOGGER = logging.getLogger(__name__)


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
                if name == "control":
                    try:
                        mode = source.read()  # type: ignore[attr-defined]
                        value = getattr(mode, "value", mode)
                        if value in {"kill", "pause"}:
                            result.update({"control_requested": str(value), "control_honored": True})
                    except Exception:
                        self._warn(name)
                else:
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


__all__ = ["RuntimeSoakFeed"]
