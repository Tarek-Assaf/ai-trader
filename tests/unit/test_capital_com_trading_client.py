"""Tests for Capital.com execution endpoint validation and request paths."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from trader.core.settings import EnvironmentSettings
from trader.integrations.capital_com.client import CapitalComError
from trader.integrations.capital_com.trading import CapitalComTradingClient


class AllowAllLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, bool]] = []

    def acquire(self, *, session: bool = False, history: bool = False) -> None:
        self.calls.append((session, history))


def make_client(
    handler: Any, limiter: AllowAllLimiter | None = None
) -> tuple[CapitalComTradingClient, httpx.Client]:
    settings = EnvironmentSettings(
        _env_file=None,
        broker_env="demo",
        capital_api_key="test-key",
        capital_identifier="test-user",
        capital_api_password="test-password",
        redis_url="redis://localhost:6379/0",
    )
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return CapitalComTradingClient(
        settings, limiter=limiter or AllowAllLimiter(), http_client=http
    ), http


def test_trading_reads_and_writes_use_authenticated_demo_routes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/session"):
            return httpx.Response(
                200,
                headers={"CST": "session-cst", "X-SECURITY-TOKEN": "session-token"},
                request=request,
            )
        if request.url.path.endswith("/positions") and request.method == "GET":
            body: Mapping[str, object] = {"positions": []}
        elif request.url.path.endswith("/workingorders"):
            body = {"workingOrders": []}
        elif request.url.path.endswith("/history/activity"):
            body = {"activities": []}
        elif request.url.path.endswith("/confirms/open ref"):
            body = {"dealStatus": "ACCEPTED"}
        elif request.method in {"POST", "PUT", "DELETE"}:
            body = {"dealReference": "open ref"}
        else:
            raise AssertionError(f"unexpected request: {request.method} {request.url.path}")
        return httpx.Response(200, json=body, request=request)

    limiter = AllowAllLimiter()
    client, http = make_client(handler, limiter)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    try:
        assert client.get_positions() == []
        assert client.get_working_orders() == []
        assert client.get_activity(now, now + timedelta(hours=1)) == []
        created = client.open_position(
            {
                "epic": "US100",
                "direction": "BUY",
                "size": 1.0,
                "guaranteedStop": False,
                "stopLevel": 99.0,
            }
        )
        assert client.confirm_deal(created["dealReference"])["dealStatus"] == "ACCEPTED"
        assert (
            client.modify_position("deal-1", {"guaranteedStop": False, "stopLevel": 100})[
                "dealReference"
            ]
            == "open ref"
        )
        assert client.close_position("deal-1")["dealReference"] == "open ref"
        assert client.cancel_working_order("work-1")["dealReference"] == "open ref"
    finally:
        client.close()
        http.close()

    assert all(request.headers.get("CST") == "session-cst" for request in requests[1:])
    assert requests[-1].url.host == "demo-api-capital.backend-capital.com"
    assert any(history for _, history in limiter.calls)


@pytest.mark.parametrize(
    "payload",
    [
        {"guaranteedStop": False},
        {"guaranteedStop": True, "stopLevel": 99},
        {"guaranteedStop": False, "stopLevel": True},
        {"guaranteedStop": False, "stopLevel": float("inf")},
        {"guaranteedStop": False, "stopDistance": 0},
    ],
)
def test_open_position_requires_explicit_non_guaranteed_finite_stop(
    payload: Mapping[str, object],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"CST": "cst", "X-SECURITY-TOKEN": "token"},
            request=request,
        )

    client, http = make_client(handler)
    try:
        with pytest.raises(ValueError, match="require guaranteedStop"):
            client.open_position(payload)
    finally:
        client.close()
        http.close()
    assert requests == []


def test_remote_list_shape_is_validated_instead_of_returning_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/session"):
            return httpx.Response(
                200,
                headers={"CST": "cst", "X-SECURITY-TOKEN": "token"},
                request=request,
            )
        return httpx.Response(200, json={"unexpected": []}, request=request)

    client, http = make_client(handler)
    try:
        with pytest.raises(CapitalComError, match="HTTP 200 for positions"):
            client.get_positions()
    finally:
        client.close()
        http.close()


def test_activity_range_must_be_utc_aware_and_at_most_one_day() -> None:
    client, http = make_client(lambda request: httpx.Response(200, request=request))
    try:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="at most one day"):
            client.get_activity(now, now + timedelta(days=1, seconds=1))
        with pytest.raises(ValueError, match="timezone-aware"):
            client.get_activity(datetime(2026, 1, 1), now)
    finally:
        client.close()
        http.close()


def test_reduce_position_sends_a_stopless_netting_order_but_nothing_else() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/session"):
            return httpx.Response(
                200, headers={"CST": "cst", "X-SECURITY-TOKEN": "token"}, request=request
            )
        return httpx.Response(200, json={"dealReference": "net ref"}, request=request)

    client, http = make_client(handler)
    try:
        reply = client.reduce_position(
            {"epic": "US100", "direction": "SELL", "size": 0.001, "guaranteedStop": False}
        )
        assert reply["dealReference"] == "net ref"
        for bad in (
            {"epic": "US100", "direction": "SELL", "size": 0.001},
            {"epic": "US100", "direction": "SELL", "size": 0, "guaranteedStop": False},
            {"epic": "US100", "direction": "HOLD", "size": 1, "guaranteedStop": False},
            {
                "epic": "US100",
                "direction": "SELL",
                "size": 1,
                "guaranteedStop": False,
                "stopLevel": 5,
            },
        ):
            with pytest.raises(ValueError, match="netting order"):
                client.reduce_position(bad)
    finally:
        client.close()
        http.close()
