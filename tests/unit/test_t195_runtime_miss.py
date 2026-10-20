from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scripts import lab_t187_runtime_replay as replay
from scripts.lab_t195_runtime_miss import (
    _classify_cause,
    _first_gate,
    _nearest_entry,
    _provider_trace,
)
from trader.experiments import tai_wiring
from trader.experiments.tai_demo import ClosedBar


def test_first_gate_uses_provider_order() -> None:
    assert (
        _first_gate(
            {
                "history_ready": True,
                "indicators_ready": True,
                "ha_cross": True,
                "ema_side": True,
                "adx_threshold": False,
                "di_direction": False,
            }
        )
        == "adx_threshold"
    )


def test_first_gate_is_none_when_all_gates_pass() -> None:
    gates = {
        name: True
        for name in (
            "history_ready",
            "indicators_ready",
            "ha_cross",
            "ema_side",
            "adx_threshold",
            "di_direction",
            "vwap_side",
            "vwap_distance",
            "swing_window",
            "risk_positive",
            "risk_cap",
        )
    }
    assert _first_gate(gates) is None


def test_classify_cause_names_the_first_runtime_gate() -> None:
    assert (
        _classify_cause(
            expected=replay.RuntimeEntry(
                datetime(2026, 6, 1, tzinfo=UTC), "SELL", Decimal("100"), Decimal("101")
            ),
            runtime=None,
            unbounded_match=None,
            trace={"first_failing_gate": "ha_cross"},
            bar_index=400,
        )
        == "runtime_gate_ha_cross"
    )


def test_nearest_entry_preserves_port_side_for_runtime_evidence() -> None:
    stamp = datetime(2026, 6, 1, tzinfo=UTC)
    expected = replay.RuntimeEntry(stamp, "SELL", Decimal("100"), Decimal("101"))
    candidate = replay.RuntimeEntry(stamp.replace(minute=3), "SELL", Decimal("99"), Decimal("102"))
    assert _nearest_entry(expected, (candidate,)) == candidate


def test_provider_trace_replays_exit_callbacks(monkeypatch) -> None:
    exits: list[datetime] = []

    class FakeProvider:
        warmup_bars = 300
        last_exit_at = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)

        def note_exit(self, at: datetime) -> None:
            exits.append(at)

        def __call__(self, bar: ClosedBar):
            return None

    monkeypatch.setattr(tai_wiring, "TaiSignalProvider", FakeProvider)
    first = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    bars = tuple(
        ClosedBar(
            at=first + timedelta(minutes=3 * index),
            epic="NSXUSD",
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            timeframe_minutes=3,
            closed=True,
            market_open=True,
            in_break=False,
            volume=Decimal("1"),
        )
        for index in range(2)
    )
    _provider_trace(bars, {bars[-1].at}, (first,))
    assert exits == [first]
