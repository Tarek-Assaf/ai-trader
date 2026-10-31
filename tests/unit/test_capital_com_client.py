"""Tests for the Capital.com REST client and shared rate limiter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from trader.core.settings import EnvironmentSettings
from trader.integrations.capital_com import (
    CapitalComClient,
    CapitalComError,
    Resolution,
)
from trader.integrations.capital_com.rate_limit import RateLimitError, RedisRateLimiter


class AllowAllLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, bool]] = []

    def acquire(self, *, session: bool = False, history: bool = False) -> None:
        self.calls.append((session, history))


def make_settings(**kwargs: object) -> EnvironmentSettings:
    defaults: dict[str, object] = {
        "_env_file": None,
        "broker_env": "demo",
        "capital_api_key": "key-secret",
        "capital_identifier": "email-secret",
        "capital_api_password": "password-secret",
        "redis_url": "redis://private-user:private-password@localhost:6379/0",
    }
    defaults.update(kwargs)
    return EnvironmentSettings(**defaults)


def response(
    request: httpx.Request,
    body: Mapping[str, Any] | None = None,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(status, json=body or {}, headers=headers, request=request)


def test_session_and_market_search_use_authenticated_demo_host() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/session") and request.method == "POST":
            return response(
                request,
                headers={"CST": "cst-private", "X-SECURITY-TOKEN": "token-private"},
            )
        if request.url.path.endswith("/markets"):
            return response(request, {"markets": [{"epic": "US100"}]})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    limiter = AllowAllLimiter()
    http = httpx.Client(transport=httpx.MockTransport(handler))
    with CapitalComClient(make_settings(), limiter=limiter, http_client=http) as client:
        result = client.search_markets("NASDAQ")
        assert client.environment == "demo"

    assert result == [{"epic": "US100"}]
    assert seen[0].url.host == "demo-api-capital.backend-capital.com"
    assert seen[0].headers["X-CAP-API-KEY"] == "key-secret"
    assert seen[1].headers["CST"] == "cst-private"
    assert seen[1].headers["X-SECURITY-TOKEN"] == "token-private"
    assert limiter.calls == [(True, False), (False, False)]


def test_unauthorized_request_recreates_session_once() -> None:
    sessions = 0
    market_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sessions, market_calls
        if request.method == "POST":
            sessions += 1
            return response(
                request,
                headers={"CST": f"cst-{sessions}", "X-SECURITY-TOKEN": f"token-{sessions}"},
            )
        market_calls += 1
        if market_calls == 1:
            return response(request, status=401)
        assert request.headers["CST"] == "cst-2"
        return response(request, {"markets": []})

    with CapitalComClient(
        make_settings(),
        limiter=AllowAllLimiter(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as client:
        assert client.search_markets("Gold") == []

    assert sessions == 2
    assert market_calls == 2


def test_price_history_pages_forward_and_uses_documented_resolution() -> None:
    queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return response(request, headers={"CST": "cst", "X-SECURITY-TOKEN": "token"})
        queries.append(dict(request.url.params))
        if len(queries) == 1:
            return response(
                request,
                {
                    "prices": [
                        {"snapshotTimeUTC": "2026-01-01T00:00:00", "closePrice": {"bid": 1}},
                        {"snapshotTimeUTC": "2026-01-01T00:01:00", "closePrice": {"bid": 2}},
                    ]
                },
            )
        return response(request, {"prices": []})

    limiter = AllowAllLimiter()
    http = httpx.Client(transport=httpx.MockTransport(handler))
    from datetime import UTC, datetime

    with CapitalComClient(make_settings(), limiter=limiter, http_client=http) as client:
        candles = list(
            client.iter_price_history(
                "US100",
                Resolution.MINUTE,
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
                page_size=2,
                max_pages=3,
            )
        )

    assert len(candles) == 2
    assert queries[0]["max"] == "2"
    assert queries[1]["from"] == "2026-01-01T00:01:01"
    assert all(call == (False, True) or call == (True, False) for call in limiter.calls)
    assert sum(history for _, history in limiter.calls) == 2


def test_price_history_reads_a_long_gap_in_bounded_windows() -> None:
    from datetime import UTC, datetime, timedelta

    queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return response(request, headers={"CST": "cst", "X-SECURITY-TOKEN": "token"})
        queries.append(dict(request.url.params))
        if len(queries) == 2:
            return response(
                request,
                {"prices": [{"snapshotTimeUTC": "2026-01-01T13:00:00", "closePrice": {"bid": 1}}]},
            )
        return response(request, {"prices": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with CapitalComClient(make_settings(), limiter=AllowAllLimiter(), http_client=http) as client:
        candles = list(
            client.iter_price_history(
                "US100",
                Resolution.MINUTE,
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, 6, tzinfo=UTC),
            )
        )

    assert len(candles) == 1
    assert len(queries) == 3
    for query in queries:
        span = datetime.fromisoformat(query["to"]) - datetime.fromisoformat(query["from"])
        assert span <= timedelta(hours=12)
    assert queries[0]["from"] == "2026-01-01T00:00:00"
    assert queries[-1]["to"] == "2026-01-02T06:00:00"
    assert queries[1]["from"] == "2026-01-01T12:00:01"


def test_live_host_is_selected_only_by_explicit_environment() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response(request, headers={"CST": "cst", "X-SECURITY-TOKEN": "token"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with CapitalComClient(
        make_settings(broker_env="live"), limiter=AllowAllLimiter(), http_client=http
    ) as client:
        assert client.environment == "live"


def test_errors_never_include_api_credentials_or_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return response(request, headers={"CST": "cst", "X-SECURITY-TOKEN": "token"})
        return httpx.Response(
            500,
            text="password-secret token-private key-secret",
            request=request,
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with CapitalComClient(make_settings(), limiter=AllowAllLimiter(), http_client=http) as client:
        with pytest.raises(CapitalComError) as error:
            client.ping()

    rendered = str(error.value) + caplog.text
    assert "password-secret" not in rendered
    assert "key-secret" not in rendered
    assert "token-private" not in rendered


def test_invalid_history_arguments_fail_before_request() -> None:
    with CapitalComClient(
        make_settings(), limiter=AllowAllLimiter(), http_client=httpx.Client()
    ) as client:
        from datetime import UTC, datetime

        with pytest.raises(ValueError, match="unsupported"):
            list(
                client.iter_price_history(
                    "US100",
                    "MINUTE_3",
                    datetime(2026, 1, 1, tzinfo=UTC),
                    datetime(2026, 1, 2, tzinfo=UTC),
                )
            )


class FakeRedis:
    def __init__(self, replies: list[int]) -> None:
        self.replies = replies
        self.calls: list[tuple[int, tuple[object, ...]]] = []

    def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object:
        assert "ZREMRANGEBYSCORE" in script
        self.calls.append((numkeys, keys_and_args))
        return self.replies.pop(0)


def test_redis_limiter_waits_and_applies_history_and_global_windows() -> None:
    redis = FakeRedis([150, 0])
    sleeps: list[float] = []
    limiter = RedisRateLimiter(
        redis,
        key_prefix="capital_com:test",
        sleep=sleeps.append,
        clock=lambda: 100.0,
    )

    limiter.acquire(history=True)

    assert sleeps == [0.15]
    assert redis.calls[0][0] == 2
    assert redis.calls[0][1][:2] == ("capital_com:test:requests", "capital_com:test:history")
    assert redis.calls[0][1][-2:] == ("10", "5")


def test_redis_limiter_includes_session_budget_and_fails_closed() -> None:
    redis = FakeRedis([0])
    limiter = RedisRateLimiter(redis, sleep=lambda _: None, clock=lambda: 100.0)
    limiter.acquire(session=True)

    assert redis.calls[0][0] == 2
    assert redis.calls[0][1][:2] == ("capital_com:requests", "capital_com:sessions")
    assert redis.calls[0][1][-2:] == ("10", "1")

    def unavailable(*_: object) -> object:
        raise RuntimeError("redis://user:secret@example")

    with pytest.raises(RateLimitError, match="unavailable") as error:
        RedisRateLimiter(unavailable).acquire()
    assert "secret" not in str(error.value)
