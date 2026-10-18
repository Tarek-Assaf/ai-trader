"""Known-answer checks for the diagnostic T-158 helpers."""

from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from scripts.lab_t158_pipeline_audit import (
    _independent_atr,
    _plain_aggregate,
    _positive_control,
    _tiny_stage3,
    stage5,
)
from trader.analysis import indicators


def _bars(count: int = 6) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(start + timedelta(minutes=index), 10 + index, 12 + index, 9 + index, 11 + index, 1.0, ("T158",), True, 1)
        for index in range(count)
    ]


def test_plain_aggregation_matches_production_for_complete_fixture() -> None:
    bars = _bars(6)
    assert tuple(item.ts_utc for item in _plain_aggregate(bars, 3)) == (
        bars[0].ts_utc,
        bars[3].ts_utc,
    )
    production = tuple(item for item in __import__("trader.data.resample", fromlist=["resample_ohlc"]).resample_ohlc(bars, 3) if item.complete)
    assert production == _plain_aggregate(bars, 3)


def test_indicator_tiny_known_answers_are_exact() -> None:
    bars = _bars(6)
    assert indicators.atr(bars, 3) == _independent_atr(bars, 3)
    assert _tiny_stage3()["ema3"]["pass"]


def test_positive_control_breaks_when_known_rule_breaks() -> None:
    result = _positive_control()
    assert result["known_signal"] is True
    assert result["break_close_rule"] is True


def test_engine_hand_ledger_matches_production() -> None:
    result = stage5()
    assert result["hand_ledger"]["pass"] is True
    assert result["buy_and_hold"]["difference"] == 0.0
