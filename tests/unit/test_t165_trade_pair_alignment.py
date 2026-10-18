from __future__ import annotations

from datetime import UTC, datetime

from scripts.lab_t165_trade_pair_alignment import OwnerTrade, _within_tolerance
from trader.data.resample import Candle


def test_signed_move_and_tolerance() -> None:
    trade = OwnerTrade("1", "short", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, 0, 3, tzinfo=UTC), 100.0, 90.0, 1.0, None, None, 1, None, None)
    assert trade.signed_move == 10.0
    assert _within_tolerance(12.0, trade.signed_move)
    assert not _within_tolerance(-12.0, trade.signed_move)


def test_candle_shape() -> None:
    candle = Candle(datetime(2024, 1, 1, tzinfo=UTC), 10.0, 13.0, 9.0, 12.0, 0.0, ("NAS100", "mid"), True, 1)
    assert candle.open == 10.0
    assert candle.high == 13.0
