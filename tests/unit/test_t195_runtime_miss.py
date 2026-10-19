from datetime import UTC, datetime
from decimal import Decimal

from scripts import lab_t187_runtime_replay as replay
from scripts.lab_t195_runtime_miss import _classify_cause, _first_gate, _nearest_entry


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
    candidate = replay.RuntimeEntry(
        stamp.replace(minute=3), "SELL", Decimal("99"), Decimal("102")
    )
    assert _nearest_entry(expected, (candidate,)) == candidate
