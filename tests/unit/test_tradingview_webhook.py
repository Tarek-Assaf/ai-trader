"""The TradingView alert route: authenticated, fresh, de-duplicated, and nothing else."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import trader.api.tradingview as tv
from trader.api.app import app as real_app
from trader.execution.brokers.base import Direction

SECRET = "s3cret-token-for-tests-0123456789"


class _Runner:
    def __init__(self) -> None:
        self.calls: list[Direction] = []

    def on_external_signal(self, direction: Direction) -> SimpleNamespace:
        self.calls.append(direction)
        return SimpleNamespace(action="SUBMITTED", reason="entry_submitted")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", SECRET)
    tv._last_accepted.clear()
    clock = {"now": datetime(2026, 10, 28, 20, 12, 4, tzinfo=UTC), "mono": 1000.0}
    monkeypatch.setattr(tv, "_now", lambda: clock["now"])
    monkeypatch.setattr(tv, "_monotonic", lambda: clock["mono"])
    app = FastAPI()
    app.include_router(tv.router)
    runner = _Runner()
    app.state.tai_demo_runtime = SimpleNamespace(runner=runner)
    test_client = TestClient(app)
    test_client.runner = runner
    test_client.clock = clock
    return test_client


def _post(client, **body):
    return client.post("/webhooks/tradingview", json=body)


def test_valid_buy_alert_reaches_the_runner(client) -> None:
    response = _post(client, secret=SECRET, signal="buy")
    assert response.status_code == 200
    assert response.json()["status"] == "SUBMITTED"
    assert client.runner.calls == [Direction.BUY]


def test_wrong_or_missing_secret_is_forbidden_and_trades_nothing(client) -> None:
    assert _post(client, secret="nope", signal="BUY").status_code == 403
    assert _post(client, signal="BUY").status_code == 403
    assert client.runner.calls == []


def test_route_is_off_when_no_secret_is_configured(client, monkeypatch) -> None:
    monkeypatch.delenv("TRADINGVIEW_WEBHOOK_SECRET")
    assert _post(client, secret=SECRET, signal="BUY").status_code == 404
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "short")
    assert _post(client, secret="short", signal="BUY").status_code == 404


def test_other_events_are_acknowledged_but_never_trade(client) -> None:
    for name in ("TP1", "TP3", "STOP LOSS", "BREAK EVEN", ""):
        response = _post(client, secret=SECRET, signal=name)
        assert response.json()["status"] == "ignored"
    assert client.runner.calls == []


def test_a_late_alert_is_not_traded(client) -> None:
    client.clock["now"] = datetime(2026, 10, 28, 20, 14, 58, tzinfo=UTC)  # 178 s into the bar
    assert _post(client, secret=SECRET, signal="SELL").json()["status"] == "stale"
    assert client.runner.calls == []


def test_a_repeated_alert_is_traded_once(client) -> None:
    assert _post(client, secret=SECRET, signal="BUY").json()["status"] == "SUBMITTED"
    assert _post(client, secret=SECRET, signal="BUY").json()["status"] == "duplicate"
    client.clock["mono"] += 200
    assert _post(client, secret=SECRET, signal="BUY").json()["status"] == "SUBMITTED"
    assert client.runner.calls == [Direction.BUY, Direction.BUY]


def test_malformed_and_oversized_bodies_are_rejected(client) -> None:
    assert client.post("/webhooks/tradingview", content=b"TAI TRADING BUY").status_code == 400
    assert client.post("/webhooks/tradingview", content=b"[1,2]").status_code == 400
    big = b'{"secret":"' + b"x" * 5000 + b'"}'
    assert client.post("/webhooks/tradingview", content=big).status_code == 413


def test_runtime_not_ready_is_a_503(client) -> None:
    client.app.state.tai_demo_runtime = None
    assert _post(client, secret=SECRET, signal="BUY").status_code == 503


def test_requests_through_a_public_tunnel_reach_only_the_alert_route(monkeypatch) -> None:
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", SECRET)
    local = TestClient(real_app)
    tunnelled = {"X-Forwarded-For": "52.89.214.238"}
    assert local.get("/health", headers=tunnelled).status_code == 404
    kill = local.post("/control/kill", headers=tunnelled, json={"totp_code": "123456"})
    assert kill.status_code == 404
    # the alert route itself is reachable through the tunnel (wrong secret -> 403)
    wrong = local.post("/webhooks/tradingview", headers=tunnelled, json={"secret": "x"})
    assert wrong.status_code == 403
    assert local.get("/health").status_code == 200


def test_symbol_routes_the_alert_to_that_instruments_runner(client) -> None:
    us100, gold = _Runner(), _Runner()
    client.app.state.tai_demo_runtime = SimpleNamespace(
        runner=us100,
        adapters={
            "US100": SimpleNamespace(runner=us100),
            "GOLD": SimpleNamespace(runner=gold),
        },
    )
    assert _post(client, secret=SECRET, signal="SELL", symbol="XAUUSD").json()["status"] == (
        "SUBMITTED"
    )
    assert _post(client, secret=SECRET, signal="BUY", symbol="NAS100").json()["status"] == (
        "SUBMITTED"
    )
    assert gold.calls == [Direction.SELL] and us100.calls == [Direction.BUY]
    assert _post(client, secret=SECRET, signal="BUY", symbol="DAX").status_code == 503
    assert _post(client, secret=SECRET, signal="BUY", symbol="NAS100").json()["status"] == (
        "duplicate"
    )
