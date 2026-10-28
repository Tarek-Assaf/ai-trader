"""The owner-run Capital.com demo chain check passes against a faithful fake broker."""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from test_capital_runtime import FakeCapital


def _load_script() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts" / "demo_chain_check.py"
    spec = importlib.util.spec_from_file_location("demo_chain_check", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LiveShapedCapital(FakeCapital):
    """FakeCapital plus the market data endpoints the chain check reads."""

    def __init__(self, *, netting: bool = True) -> None:
        super().__init__(netting=netting)
        self.closed = False

    def search_markets(self, epic: str) -> list[dict[str, Any]]:
        return [{"epic": epic, "tickSize": "0.1"}]

    def market_details(self, epic: str) -> dict[str, Any]:
        return {
            "instrument": {"epic": epic, "lotSize": 1},
            "dealingRules": {
                "minDealSize": {"value": "0.001", "unit": "POINTS"},
                "minSizeIncrement": {"value": "0.001", "unit": "POINTS"},
                "minStopOrProfitDistance": {"value": "0.01", "unit": "PERCENTAGE"},
            },
            "snapshot": {"bid": "29782.7", "offer": "29784.8", "decimalPlacesFactor": 1},
        }

    def open_position(self, payload: dict[str, Any]) -> dict[str, Any]:
        reply = super().open_position(payload)
        # the fake fills at the quoted offer
        for position in self.positions.values():
            if position["direction"] == "BUY" and position["level"] == Decimal("100"):
                position["level"] = Decimal("29784.8")
        return reply

    def close(self) -> None:
        self.closed = True


def test_chain_check_passes_and_leaves_the_account_flat() -> None:
    script = _load_script()
    client = LiveShapedCapital()
    report = script.run(SimpleNamespace(broker_env="demo"), client=client)
    assert report["result"] == "pass", report
    assert report["flat_at_end"] is True
    assert client.positions == {}
    names = [entry["step"] for entry in report["steps"]]
    assert names == [
        "account",
        "flat_before",
        "rules",
        "open_with_stop_and_target",
        "stop_only_modify",
        "stop_and_target_modify",
        "partial_close",
        "flat_after",
        "account_after",
    ]
    assert client.closed


def test_chain_check_cleans_up_and_fails_when_the_broker_hedges_instead_of_netting() -> None:
    script = _load_script()
    client = LiveShapedCapital(netting=False)
    report = script.run(SimpleNamespace(broker_env="demo"), client=client)
    assert report["result"] == "fail"
    assert "net" in report["error"].lower()
    assert report["flat_at_end"] is True
    assert client.positions == {}


def test_chain_check_refuses_a_non_flat_account() -> None:
    script = _load_script()
    client = LiveShapedCapital()
    client.open_position({"epic": "US100", "direction": "BUY", "size": 0.001, "stopLevel": 1.0})
    report = script.run(SimpleNamespace(broker_env="demo"), client=client)
    assert report["result"] == "fail"
    assert "not flat" in report["error"]
