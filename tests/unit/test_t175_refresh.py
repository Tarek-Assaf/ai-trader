from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from scripts import lab_t162_pine_port as port
from scripts.lab_t151_owner_trades import OwnerEntry
from scripts.lab_t162_pine_port import OwnerExit
from scripts.lab_t175_refresh import classify, condition_values

from trader.data.resample import Candle


def _bars(count=30):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(start + timedelta(minutes=3 * i), 100, 101, 99, 100, n_minutes=3)
        for i in range(count)
    )


def test_classify_marks_recent_exit_as_refresh():
    bars = _bars()
    owners = (
        OwnerEntry(bars[2].ts_utc, "long", 100, 1),
        OwnerEntry(bars[15].ts_utc, "long", 100, 1),
    )
    exits = (OwnerExit("1", "long", bars[3].ts_utc, bars[10].ts_utc, "target"),)
    states = classify(owners, exits, bars)
    assert [row["class"] for row in states] == ["first", "refresh"]
    assert states[1]["bars_since_same_direction_exit"] == 5
    assert states[0]["bar_index"] == 2


def test_first_direction_uses_cross_and_refresh_does_not(monkeypatch):
    monkeypatch.setattr(port, "_risk", lambda *args: (True, 1.0))
    bars = _bars()
    features = SimpleNamespace(
        ema=(100.0,) * len(bars),
        atr=(1.0,) * len(bars),
        adx=(30.0,) * len(bars),
        plus_di=(25.0,) * len(bars),
        minus_di=(10.0,) * len(bars),
        vwap=(99.0,) * len(bars),
        buy_cross=(False,) * len(bars),
        sell_cross=(False,) * len(bars),
    )
    exits = ()
    first = condition_values(bars, features, 10, "long", "first", exits)
    refresh = condition_values(bars, features, 10, "long", "refresh", exits)
    assert first["direction"] is False
    assert refresh["direction"] is True
