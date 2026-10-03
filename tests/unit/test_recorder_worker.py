"""Worker-side Capital.com market-status validation tests."""

from __future__ import annotations

import pytest

from trader.workers.recorder import _combined_market_status, _market_status


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"marketStatus": ""},
        {"marketStatus": 123},
        {"market": {"marketStatus": "x" * 31}},
    ],
)
def test_invalid_market_status_is_unknown(payload: object) -> None:
    assert _market_status(payload) == "unknown"


def test_market_status_accepts_top_level_or_market_shape() -> None:
    assert _market_status({"marketStatus": " TRADEABLE "}) == "TRADEABLE"
    assert _market_status({"market": {"marketStatus": "CLOSED"}}) == "CLOSED"


def test_combined_status_fails_closed_for_missing_or_oversized_values() -> None:
    assert _combined_market_status([]) == "unknown"
    assert _combined_market_status(["TRADEABLE", "unknown"]) == "unknown"
    assert _combined_market_status(["x" * 30, "y" * 11]) == "unknown"


def test_combined_status_reports_each_configured_epic() -> None:
    assert _combined_market_status(["TRADEABLE", "CLOSED"]) == "TRADEABLE,CLOSED"



def test_market_status_reads_capital_com_snapshot_shape() -> None:
    assert _market_status({"snapshot": {"marketStatus": "TRADEABLE"}}) == "TRADEABLE"
    assert _market_status({"snapshot": {"marketStatus": "CLOSED"}}) == "CLOSED"
    assert _market_status({"snapshot": {"marketStatus": "UNRECOGNIZED"}}) == "unknown"
