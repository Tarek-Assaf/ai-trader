from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import httpx
import pytest

from trader.core.settings import EnvironmentSettings
from trader.integrations.capital_com import client as capital_client
from trader.integrations.capital_com.client import CapitalComClient, CapitalComError
from trader.integrations.capital_com.rate_limit import RedisRateLimiter


class AllowAllLimiter:
    def acquire(self, *, session: bool = False, history: bool = False) -> None:
        return None


def settings() -> EnvironmentSettings:
    return EnvironmentSettings(
        _env_file=None,
        broker_env="demo",
        capital_api_key="key-secret",
        capital_identifier="email-secret",
        capital_api_password="password-secret",
        redis_url="redis://localhost:6379/0",
    )


def reply(
    request: httpx.Request,
    body: Mapping[str, Any] | None = None,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(status, json=body or {}, headers=headers, request=request)


def test_read_429_honours_retry_after_then_recovers() -> None:
    calls: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return reply(request, status=429, headers={"Retry-After": "2.5"})
        if request.url.path.endswith("/session"):
            return reply(request, headers={"CST": "cst", "X-SECURITY-TOKEN": "token"})
        return reply(request, {"markets": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with CapitalComClient(
        settings(),
        limiter=AllowAllLimiter(),
        http_client=client,
        sleep=sleeps.append,
        jitter=lambda _: 0.0,
    ) as capital:
        assert capital.search_markets("US100") == []

    assert sleeps == [2.5]
    assert [request.method for request in calls] == ["POST", "POST", "GET"]


def test_repeated_read_429_is_typed_and_uses_four_bounded_attempts() -> None:
    calls: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return reply(request, status=429)

    with pytest.raises(CapitalComError) as raised:
        with CapitalComClient(
            settings(),
            limiter=AllowAllLimiter(),
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleep=sleeps.append,
            jitter=lambda _: 0.0,
        ) as capital:
            capital.search_markets("US100")

    assert raised.value.status_code == 429
    assert raised.value.attempt == 4
    assert len(calls) == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_order_mutation_429_is_not_retried() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return reply(request, status=429)

    with CapitalComClient(
        settings(),
        limiter=AllowAllLimiter(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as capital:
        with pytest.raises(CapitalComError):
            capital._send("POST", "positions", json={}, headers={}, retry_429=False)

    assert [request.method for request in calls] == ["POST"]


@pytest.fixture
def isolated_capital_session_cache() -> Iterator[None]:
    with capital_client._SHARED_SESSION_LOCK:
        snapshot = capital_client._SHARED_SESSIONS.copy()
        capital_client._SHARED_SESSIONS.clear()
    try:
        yield
    finally:
        with capital_client._SHARED_SESSION_LOCK:
            capital_client._SHARED_SESSIONS.clear()
            capital_client._SHARED_SESSIONS.update(snapshot)


def test_two_clients_reuse_one_session_with_shared_http_client(
    isolated_capital_session_cache: None,
) -> None:
    sessions = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sessions
        if request.url.path.endswith("/session"):
            sessions += 1
            return reply(request, headers={"CST": "cst", "X-SECURITY-TOKEN": "token"})
        return reply(request, {"markets": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with CapitalComClient(settings(), limiter=AllowAllLimiter(), http_client=http) as first:
        with CapitalComClient(settings(), limiter=AllowAllLimiter(), http_client=http) as second:
            assert first.search_markets("US100") == []
            assert second.search_markets("US100") == []
    assert sessions == 1


class FakeRedis:
    def __init__(self) -> None:
        self.calls = 0

    def eval(self, script: str, numkeys: int, *args: object) -> object:
        self.calls += 1
        assert "ZREMRANGEBYSCORE" in script
        assert numkeys == 1
        return 0


def test_burst_calls_always_use_the_shared_limiter() -> None:
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis, key_prefix="capital_com:test", sleep=lambda _: None)
    for _ in range(50):
        limiter.acquire()
    assert redis.calls == 50
