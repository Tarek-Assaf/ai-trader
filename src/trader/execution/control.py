"""Independent, deterministic sources for the execution kill switch."""

from __future__ import annotations

import hmac
import os
import time
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from trader.control.totp import matching_totp_step
from trader.core.accounts import ACCOUNT_ID_PATTERN


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


GLOBAL_CONTROL_KEY = "ai_trader:execution:control"


def control_key(account_id: str) -> str:
    """The Redis key that pauses, kills or resumes exactly one account."""
    if not ACCOUNT_ID_PATTERN.match(account_id):
        raise ValueError("account id must look like acct-02")
    return f"{GLOBAL_CONTROL_KEY}:{account_id}"


class RedisControlSource:
    """Read and update a persistent Redis control key shared by service workers."""

    def __init__(self, client: object, key: str = GLOBAL_CONTROL_KEY) -> None:
        if not key.strip():
            raise ValueError("control key must not be empty")
        self._client = client
        self._key = key
        self._last_requested: ControlMode | None = None
        self._last_honored: bool | None = None

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
        client: Any = self._client
        try:
            lockout_until = float(client.get(self._auth_key("lockout")) or 0)
            if lockout_until > current:
                return False
            if lockout_until:
                client.delete(self._auth_key("lockout"))
                client.delete(self._auth_key("failures"))
            token_ok = hmac.compare_digest(expected_token, provided_token)
            matched_step = matching_totp_step(totp_secret, code, now=current)
            last_step_raw = client.get(self._auth_key("last_step"))
            last_step = int(last_step_raw) if last_step_raw is not None else None
            valid = (
                token_ok
                and matched_step is not None
                and (last_step is None or matched_step > last_step)
            )
            if valid:
                client.set(self._auth_key("last_step"), str(matched_step))
                client.delete(self._auth_key("failures"))
                return True
            failures = int(client.get(self._auth_key("failures")) or 0) + 1
            if failures >= 5:
                client.set(self._auth_key("lockout"), str(current + 900))
                client.delete(self._auth_key("failures"))
            else:
                client.set(self._auth_key("failures"), str(failures))
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
        self._last_requested = mode
        try:
            if mode is ControlMode.PAUSE and self.read() is ControlMode.KILL:
                self._last_honored = False
                return
            if mode is ControlMode.RUN:
                self._client.delete(self._key)  # type: ignore[attr-defined]
            else:
                self._client.set(self._key, mode.value)  # type: ignore[attr-defined]
            self._last_honored = self.read() is mode
        except Exception:
            self._last_honored = False
            raise ControlUnavailable("Redis control key could not be updated") from None

    def soak_observation(self) -> Mapping[str, object]:
        if self._last_requested not in {ControlMode.KILL, ControlMode.PAUSE}:
            return {}
        return {
            "control_requested": self._last_requested.value,
            "control_honored": bool(self._last_honored),
        }


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

    def soak_observation(self) -> Mapping[str, object]:
        result: dict[str, object] = {}
        for source in self._sources:
            provider = getattr(source, "soak_observation", None)
            if callable(provider):
                value = provider()
                if isinstance(value, Mapping):
                    result.update(value)
        if result:
            return result
        mode = self.read()
        value = getattr(mode, "value", mode)
        return (
            {"control_requested": str(value), "control_honored": True}
            if value in {"kill", "pause"}
            else {}
        )


_SEVERITY = {
    ControlMode.RUN: 0,
    ControlMode.RESUME: 1,
    ControlMode.PAUSE: 2,
    ControlMode.KILL: 3,
}


class AllAccountsWriter:
    """Control writer for "every account": one shared key stops all, resume goes per account."""

    def __init__(
        self, global_writer: RedisControlSource, account_writers: Mapping[str, RedisControlSource]
    ) -> None:
        self._global = global_writer
        self._accounts = dict(account_writers)

    def authorize(
        self,
        expected_token: str,
        provided_token: str,
        totp_secret: str,
        code: str,
        *,
        now: float | None = None,
    ) -> bool:
        """Owner authentication shares the single set of replay and lockout counters."""
        return self._global.authorize(expected_token, provided_token, totp_secret, code, now=now)

    def set(self, mode: ControlMode) -> None:
        if mode is not ControlMode.RESUME:
            # Every account's controls read the shared key, so one write halts them all.
            self._global.set(mode)
            return
        self._global.set(ControlMode.RUN)
        for writer in self._accounts.values():
            # A killed account stays killed: only a deliberate, per-account step clears a kill.
            if writer.read() is not ControlMode.KILL:
                writer.set(ControlMode.RESUME)


class AccountControls:
    """Pause, kill and resume for each account separately, or for all of them at once.

    All accounts share one Redis.  An account obeys its own key, the shared key and the flag
    files, with kill ahead of pause.  The Telegram process holds every account's writer; each
    account's process reads only its own.
    """

    def __init__(self, client: object, account_ids: Sequence[str], flag_dir: Path) -> None:
        if not account_ids:
            raise ValueError("at least one account is required")
        self._flags = FlagFileControlSource(flag_dir)
        self._global = RedisControlSource(client)
        self._accounts = {
            account_id: RedisControlSource(client, control_key(account_id))
            for account_id in dict.fromkeys(account_ids)
        }

    def numbers(self) -> tuple[str, ...]:
        return tuple(account_id.removeprefix("acct-") for account_id in self._accounts)

    def has(self, number: str) -> bool:
        return f"acct-{number}" in self._accounts

    def source_for(self, account_id: str) -> CompositeControlSource:
        return CompositeControlSource((self._flags, self._global, self._accounts[account_id]))

    def account_writer(self, account_id: str) -> RedisControlSource:
        return self._accounts[account_id]

    @property
    def global_writer(self) -> RedisControlSource:
        return self._global

    def writer(self, number: str | None) -> ControlWriter:
        """Writer for one account (``"02"``), or for every account when ``number`` is None."""
        if number is None:
            return AllAccountsWriter(self._global, self._accounts)
        return self._accounts[f"acct-{number}"]

    def mode(self, number: str | None) -> ControlMode:
        """What the account (or the most restrictive account) is being told to do."""
        accounts = list(self._accounts) if number is None else [f"acct-{number}"]
        worst = ControlMode.RUN
        for account_id in accounts:
            current = self.source_for(account_id).read()
            if _SEVERITY[current] > _SEVERITY[worst]:
                worst = current
        return worst

    def global_mode(self) -> ControlMode:
        return CompositeControlSource((self._flags, self._global)).read()


def build_account_controls(
    *, redis_url: str, account_ids: Sequence[str], control_dir: str | None = None
) -> AccountControls:
    """Construct the per-account controls without connecting until first use."""
    from redis import Redis

    url = redis_url.strip()
    if not url:
        raise ValueError("Redis URL is required for execution controls")
    directory = Path(control_dir or os.getenv("CONTROL_DIR") or "/workspace/control")
    return AccountControls(Redis.from_url(url, socket_connect_timeout=2), account_ids, directory)


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
