"""Independent, deterministic sources for the execution kill switch."""

from __future__ import annotations

import hmac
import os
import time
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from trader.control.totp import matching_totp_step


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

    def _auth_key(self, suffix: str) -> str:
        return f"{self._key}:auth:{suffix}"

    def authorize(
        self,
        expected_token: str,
        provided_token: str,
        totp_secret: str,
        code: str,
        *,
        now: float | None = None,
    ) -> bool:
        """Authenticate an owner action with shared replay and lockout state."""
        current = time.time() if now is None else now
        try:
            lockout_until = float(self._client.get(self._auth_key("lockout")) or 0)
            if lockout_until > current:
                return False
            if lockout_until:
                self._client.delete(self._auth_key("lockout"))
                self._client.delete(self._auth_key("failures"))
            token_ok = hmac.compare_digest(expected_token, provided_token)
            matched_step = matching_totp_step(totp_secret, code, now=current)
            last_step_raw = self._client.get(self._auth_key("last_step"))
            last_step = int(last_step_raw) if last_step_raw is not None else None
            valid = token_ok and matched_step is not None and (
                last_step is None or matched_step > last_step
            )
            if valid:
                self._client.set(self._auth_key("last_step"), str(matched_step))
                self._client.delete(self._auth_key("failures"))
                return True
            failures = int(self._client.get(self._auth_key("failures")) or 0) + 1
            if failures >= 5:
                self._client.set(self._auth_key("lockout"), str(current + 900))
                self._client.delete(self._auth_key("failures"))
            else:
                self._client.set(self._auth_key("failures"), str(failures))
            return False
        except Exception:
            raise ControlUnavailable("control authentication state is unavailable") from None

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
            if mode is ControlMode.PAUSE and self.read() is ControlMode.KILL:
                return
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
