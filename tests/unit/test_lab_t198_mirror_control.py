import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.lab_t198_mirror_control import _mirror_one


def _bar(index: int, high: float, low: float, opening: float = 100.0) -> SimpleNamespace:
    return SimpleNamespace(
        ts_utc=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
        open=opening,
        high=high,
        low=low,
        close=opening,
    )


def test_mirror_inverts_direction_and_uses_same_risk() -> None:
    bars = (_bar(0, 101.0, 98.0), _bar(1, 100.5, 99.0))
    original = SimpleNamespace(
        direction="long",
        entry_index=0,
        entry_time=bars[0].ts_utc,
        entry_price=100.0,
        stop_price=98.0,
    )

    mirror = _mirror_one(bars, original)

    assert mirror["side"] == "short"
    assert mirror["entry_price"] == 100.0
    assert mirror["risk_points"] == 2.0
    assert mirror["exit_reason"] == "be"
    assert mirror["gross_r"] == 0.0


def test_original_long_can_win_two_r_while_mirror_stops() -> None:
    bars = (_bar(0, 104.0, 99.0), _bar(1, 104.0, 101.0))
    original = SimpleNamespace(
        direction="long",
        entry_index=0,
        entry_time=bars[0].ts_utc,
        entry_price=100.0,
        stop_price=98.0,
    )

    mirror = _mirror_one(bars, original)

    assert mirror["side"] == "short"
    assert mirror["exit_reason"] == "stop"
    assert mirror["gross_r"] == -1.0
