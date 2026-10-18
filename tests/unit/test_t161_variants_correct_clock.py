"""Pure helper checks for the T-161 diagnostic."""

from datetime import UTC, datetime

from scripts.lab_t161_variants_correct_clock import _colour, _owner_verdict

from trader.data.resample import Candle


def _bar(open_price: float, close: float) -> Candle:
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    return Candle(
        stamp,
        open_price,
        max(open_price, close),
        min(open_price, close),
        close,
        0.0,
        ("x",),
        True,
        1,
    )


def test_colour_and_missing_are_explicit() -> None:
    assert _colour(_bar(1.0, 2.0)) == "long"
    assert _colour(_bar(2.0, 1.0)) == "short"
    assert _colour(_bar(1.0, 1.0)) == "neutral"
    assert _colour(None) == "missing"


def test_owner_verdict_reports_stop_threshold() -> None:
    variants = {
        "first": {"center_pass": 2, "window_pass": 3},
        "second": {"center_pass": 10, "window_pass": 20},
    }
    assert _owner_verdict(variants, 31) == {
        "best_switch": "second",
        "best_center_pass": 10,
        "best_window_pass": 20,
        "entries": 31,
        "reaches_twenty": True,
    }
