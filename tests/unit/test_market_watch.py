from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trader.runtime.market_watch import MarketWatcher


class _Client:
    def __init__(self) -> None:
        self.status = "TRADEABLE"

    def market_details(self, epic: str) -> dict[str, Any]:
        return {"snapshot": {"marketStatus": self.status}, "instrument": {"epic": epic}}


def test_alerts_once_on_close_and_once_on_open_but_not_on_first_look() -> None:
    client, sent = _Client(), []
    watcher = MarketWatcher(client, ["US100"], sent.append)
    now = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)
    watcher.poll(now)
    watcher.poll(now)
    assert sent == []
    client.status = "CLOSED"
    watcher.poll(now)
    watcher.poll(now)
    assert len(sent) == 1 and sent[0].startswith("MARKET CLOSED US100 at Thu 01:00 Riyadh")
    client.status = "TRADEABLE"
    watcher.poll(now)
    assert len(sent) == 2 and sent[1].startswith("MARKET OPEN US100")


def test_unknown_status_and_lookup_errors_stay_silent() -> None:
    class Broken:
        def market_details(self, epic: str) -> dict[str, Any]:
            raise RuntimeError("down")

    sent: list[str] = []
    MarketWatcher(Broken(), ["US100"], sent.append).poll()
    client = _Client()
    client.status = "EDITS_ONLY"
    MarketWatcher(client, ["US100"], sent.append).poll()
    assert sent == []
