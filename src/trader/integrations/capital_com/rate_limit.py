"""Redis-backed shared rate limits for Capital.com requests."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Protocol

import redis


class RateLimitError(RuntimeError):
    """Raised when the shared limiter cannot safely authorize a request."""


class RateLimiter(Protocol):
    """A shared limiter used before every request to Capital.com."""

    def acquire(self, *, session: bool = False, history: bool = False) -> None:
        """Wait until a request fits all applicable one-second windows."""


class _RedisEval(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object: ...


_ACQUIRE_SCRIPT = """
local now = tonumber(ARGV[1])
local request_id = ARGV[2]
local count = #KEYS
local waits = {}
for i = 1, count do
  local key = KEYS[i]
  local limit = tonumber(ARGV[i + 2])
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now - 1000)
  local size = redis.call('ZCARD', key)
  if size >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    waits[#waits + 1] = tonumber(oldest[2]) + 1000 - now + 1
  end
end
if #waits > 0 then
  local wait = waits[1]
  for i = 2, #waits do
    if waits[i] > wait then wait = waits[i] end
  end
  return wait
end
for i = 1, count do
  redis.call('ZADD', KEYS[i], now, request_id)
  redis.call('PEXPIRE', KEYS[i], 2000)
end
return 0
"""


class RedisRateLimiter:
    """Atomic sliding-window limiter shared by core processes through Redis.

    Every request is capped at 10/s per credential set. History reads are also
    capped at 5/s so they cannot consume more than half the shared allowance.
    Session creation has its own 1/s cap. Redis errors fail closed.
    """

    def __init__(
        self,
        client: _RedisEval,
        *,
        key_prefix: str = "capital_com",
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._prefix = key_prefix
        self._sleep = sleep
        self._clock = clock

    def acquire(self, *, session: bool = False, history: bool = False) -> None:
        keys = [f"{self._prefix}:requests"]
        limits = [10]
        if history:
            keys.append(f"{self._prefix}:history")
            limits.append(5)
        if session:
            keys.append(f"{self._prefix}:sessions")
            limits.append(1)

        while True:
            now_ms = int(self._clock() * 1000)
            try:
                delay_ms = self._client.eval(
                    _ACQUIRE_SCRIPT,
                    len(keys),
                    *keys,
                    now_ms,
                    uuid.uuid4().hex,
                    *limits,
                )
            except Exception as exc:  # Redis must fail closed; do not expose its URL.
                raise RateLimitError("Capital.com rate limiter is unavailable") from None
            try:
                delay = int(delay_ms)
            except (TypeError, ValueError) as exc:
                raise RateLimitError("Capital.com rate limiter returned an invalid delay") from exc
            if delay <= 0:
                return
            self._sleep(min(delay, 1000) / 1000)


def limiter_from_settings(
    redis_url: str | None,
    api_key: str,
    *,
    embedded_redis: bool = False,
) -> RedisRateLimiter:
    """Build a limiter whose Redis keys are scoped by a one-way API-key digest."""
    import hashlib

    if redis_url is None:
        if not embedded_redis:
            raise RateLimitError("REDIS_URL must be configured for the shared Capital.com limiter")
        redis_url = "redis://127.0.0.1:6379/0"
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:20]
    client = redis.Redis.from_url(redis_url)
    return RedisRateLimiter(client, key_prefix=f"capital_com:{digest}")
