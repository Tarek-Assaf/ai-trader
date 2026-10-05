"""Independent, deterministic sources for the execution kill switch."""

from __future__ import annotations

import os
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class ControlMode(StrEnum):
    RUN = "run"
    PAUSE = "pause"
    KILL = "kill"
    RESUME = "resume"


class ControlUnavailable(RuntimeError):
    """A configured control source could not be read safely."""


class ControlSource(Protocol):
    def read(self) -> ControlMode: ...


class ControlWriter(Protocol):
    def set(self, mode: ControlMode) -> None: ...


class FlagFileControlSource:
    """Read operator-created flags from the persistent workspace volume."""

    def __init__(self, directory: Path = Path("/workspace/control")) -> None:
        self._directory = directory

    def read(self) -> ControlMode:
        try:
            if (self._directory / "kill.flag").is_file():
                return ControlMode.KILL
            if (self._directory / "pause.flag").is_file():
                return ControlMode.PAUSE
            return ControlMode.RUN
        except OSError:
            raise ControlUnavailable("control flag files are unavailable") from None


class RedisControlSource:
    """Read and update a persistent Redis control key shared by service workers."""

    def __init__(self, client: object, key: str = "ai_trader:execution:control") -> None:
        if not key.strip():
            raise ValueError("control key must not be empty")
        self._client = client
        self._key = key

    def read(self) -> ControlMode:
        try:
            value = self._client.get(self._key)  # type: ignore[attr-defined]
        except Exception:
            raise ControlUnavailable("Redis control key is unavailable") from None
        if value is None:
            return ControlMode.RUN
        if isinstance(value, bytes):
            try:
                value = value.decode("ascii")
            except UnicodeDecodeError:
                raise ControlUnavailable("Redis control value is invalid") from None
        try:
            return ControlMode(str(value))
        except ValueError:
            raise ControlUnavailable("Redis control value is invalid") from None

    def set(self, mode: ControlMode) -> None:
        try:
            if mode is ControlMode.RUN:
                self._client.delete(self._key)  # type: ignore[attr-defined]
            else:
                self._client.set(self._key, mode.value)  # type: ignore[attr-defined]
        except Exception:
            raise ControlUnavailable("Redis control key could not be updated") from None


class CompositeControlSource:
    """Combine independent controls, with kill taking precedence over pause."""

    def __init__(self, sources: Sequence[ControlSource]) -> None:
        if not sources:
            raise ValueError("at least one control source is required")
        self._sources = tuple(sources)

    def read(self) -> ControlMode:
        modes: list[ControlMode] = []
        errors: list[ControlUnavailable] = []
        for source in self._sources:
            try:
                modes.append(source.read())
            except ControlUnavailable as exc:
                errors.append(exc)
        if ControlMode.KILL in modes:
            return ControlMode.KILL
        if ControlMode.PAUSE in modes:
            return ControlMode.PAUSE
        if ControlMode.RESUME in modes:
            return ControlMode.RESUME
        if errors:
            raise ControlUnavailable("one or more control sources are unavailable") from None
        return ControlMode.RUN


def build_control_sources(
    *, redis_url: str, control_dir: str | None = None
) -> tuple[CompositeControlSource, RedisControlSource]:
    """Construct the shared API/file sources without connecting until first use."""
    from redis import Redis

    url = redis_url.strip()
    if not url:
        raise ValueError("Redis URL is required for execution controls")
    directory = Path(control_dir or os.getenv("CONTROL_DIR") or "/workspace/control")
    redis_source = RedisControlSource(Redis.from_url(url, socket_connect_timeout=2))
    return CompositeControlSource((FlagFileControlSource(directory), redis_source)), redis_source
