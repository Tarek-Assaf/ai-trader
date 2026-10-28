"""Redis ownership leases for deterministic execution and Guardian services."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol
from uuid import uuid4


class LeaseLost(RuntimeError):
    """Ownership could not be proved; no entry may be submitted."""


class RedisLeaseClient(Protocol):
    def set(self, name: str, value: str, *, nx: bool, px: int) -> object: ...

    def eval(self, script: str, numkeys: int, *args: str | int) -> object: ...


_RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class RedisLeaderLease:
    """An expiring token lease with atomic owner-only renewal and release.

    Durable intent reservation must independently prevent duplicate submission
    if an old process stalls beyond this lease. A lease alone is not fencing.
    """

    def __init__(
        self,
        client: RedisLeaseClient,
        key: str,
        *,
        ttl_ms: int = 30_000,
        retries: int = 0,
        retry_delay_seconds: float = 1.0,
        retake_when_expired: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not key.strip() or ttl_ms <= 0 or retries < 0 or retry_delay_seconds < 0:
            raise ValueError("lease key and positive duration are required")
        self._client = client
        self._key = key
        self._ttl_ms = ttl_ms
        # A slow or briefly unreachable Redis (disk stalls, backups) must not cost the
        # lease: verification is retried, and a lapsed key nobody else took is re-taken.
        self._retries = retries
        self._retry_delay = retry_delay_seconds
        self._retake = retake_when_expired
        self._sleep = sleep
        self._token = str(uuid4())
        self._held = False

    def acquire(self) -> None:
        if self._held:
            self.check()
            return
        try:
            acquired = self._client.set(self._key, self._token, nx=True, px=self._ttl_ms)
        except Exception:
            raise LeaseLost("Redis lease acquisition failed") from None
        if acquired is not True:
            raise LeaseLost("another process owns the service lease")
        self._held = True

    def _retake_free_key(self) -> bool:
        try:
            return self._client.set(self._key, self._token, nx=True, px=self._ttl_ms) is True
        except Exception:
            return False

    def check(self) -> None:
        if not self._held:
            if self._retake and self._retake_free_key():
                self._held = True
                return
            raise LeaseLost("service lease is not held")
        attempts = self._retries + 1
        for attempt in range(attempts):
            try:
                renewed = self._client.eval(_RENEW, 1, self._key, self._token, self._ttl_ms)
            except Exception:
                if attempt < attempts - 1:
                    self._sleep(self._retry_delay * (attempt + 1))
                    continue
                self._held = False
                raise LeaseLost("Redis lease verification failed") from None
            if renewed == 1:
                return
            if self._retake and self._retake_free_key():
                return
            self._held = False
            raise LeaseLost("service lease expired or ownership changed")

    def release(self) -> None:
        self._held = False
        try:
            self._client.eval(_RELEASE, 1, self._key, self._token)
        except Exception:
            raise LeaseLost("Redis lease release failed; wait for expiry") from None
