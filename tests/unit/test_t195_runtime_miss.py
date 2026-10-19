from scripts.lab_t195_runtime_miss import _first_gate


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
