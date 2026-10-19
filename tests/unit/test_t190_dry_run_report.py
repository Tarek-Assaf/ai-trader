from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scripts import report_t190_dry_run_day as report

from trader.execution.brokers.base import Direction
from trader.experiments.tai_demo import ClosedBar, TaiSignal


def _bar(at: datetime, price: str = "100") -> ClosedBar:
    value = Decimal(price)
    return ClosedBar(at, "NSXUSD", value, value, value, value)


def _shadow(
    at: datetime,
    *,
    bid_high: str = "100",
    bid_low: str = "100",
    ask_high: str = "100",
    ask_low: str = "100",
) -> report.ShadowBar:
    return report.ShadowBar(
        _bar(at),
        Decimal(bid_high),
        Decimal(bid_low),
        Decimal("100"),
        Decimal(ask_high),
        Decimal(ask_low),
        Decimal("100"),
    )


class _Signals:
    def __init__(self, signal: TaiSignal):
        self.signal = signal

    def __call__(self, _bar: ClosedBar) -> TaiSignal | None:
        result, self.signal = self.signal, None
        return result


def _run(monkeypatch, bars, signal):
    monkeypatch.setattr(report, "TaiSignalProvider", lambda **_kwargs: _Signals(signal))
    return report.simulate(
        tuple(bars),
        bars[0].bar.at,
        bars[-1].bar.at + timedelta(minutes=3),
        Decimal("10000"),
        Decimal("0.1"),
        Decimal("0.1"),
        Decimal("1"),
    )[0]


def test_long_hits_all_targets(monkeypatch):
    start = datetime(2026, 10, 19, 15, tzinfo=UTC)
    bars = [
        _shadow(start),
        _shadow(start + timedelta(minutes=3), ask_high="101"),
        _shadow(start + timedelta(minutes=6), ask_high="102"),
        _shadow(start + timedelta(minutes=9), ask_high="103"),
    ]
    trades = _run(monkeypatch, bars, TaiSignal(Direction.BUY, Decimal("100"), Decimal("99")))
    assert trades[0].hit == [1, 2, 3]
    assert trades[0].reason == "target_3"


def test_long_stops_out_and_marks_ambiguity(monkeypatch):
    start = datetime(2026, 10, 19, 15, tzinfo=UTC)
    bars = [_shadow(start), _shadow(start + timedelta(minutes=3), bid_low="99", ask_high="101")]
    trades = _run(monkeypatch, bars, TaiSignal(Direction.BUY, Decimal("100"), Decimal("99")))
    assert trades[0].reason == "stop"
    assert trades[0].ambiguous is True


def test_short_moves_stop_to_entry_after_tp1(monkeypatch):
    start = datetime(2026, 10, 19, 15, tzinfo=UTC)
    bars = [
        _shadow(start),
        _shadow(start + timedelta(minutes=3), bid_low="99"),
        _shadow(start + timedelta(minutes=6), ask_high="100"),
    ]
    trades = _run(monkeypatch, bars, TaiSignal(Direction.SELL, Decimal("100"), Decimal("101")))
    assert trades[0].hit == [1]
    assert trades[0].reason == "stop"


def test_both_stop_and_target_is_stop_first(monkeypatch):
    start = datetime(2026, 10, 19, 15, tzinfo=UTC)
    bars = [_shadow(start), _shadow(start + timedelta(minutes=3), bid_low="99", ask_high="101")]
    trades = _run(monkeypatch, bars, TaiSignal(Direction.BUY, Decimal("100"), Decimal("99")))
    assert trades[0].reason == "stop"
    assert trades[0].ambiguous is True
