"""Focused pure-reference tests for T-155."""

from datetime import UTC, datetime, timedelta

from scripts.lab_t155_tai_parity import (
    independent_adx_di,
    independent_atr,
    independent_ema,
    independent_heikin_ashi,
    session_vwap,
)

from trader.data.resample import Candle


def _bars(count: int = 40) -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(
            start + timedelta(minutes=3 * index),
            100.0 + index,
            101.0 + index,
            99.0 + index,
            100.5 + index,
            float(index + 1),
            ("NAS100", "mid"),
            True,
            3,
        )
        for index in range(count)
    )


def test_independent_ema_uses_sma_seed() -> None:
    values = (1.0, 2.0, 3.0, 4.0)
    assert independent_ema(values, 3) == (None, None, 2.0, 3.0)


def test_independent_atr_and_adx_have_wilder_warmup() -> None:
    bars = _bars()
    atr = independent_atr(bars, 14)
    adx, plus, minus = independent_adx_di(bars, 14)
    assert atr[:13] == (None,) * 13
    assert atr[13] == 2.0
    assert plus[13] is not None and minus[13] == 0.0
    assert adx[:26] == (None,) * 26
    assert adx[26] == 100.0


def test_heikin_ashi_reference_is_deterministic() -> None:
    bars = _bars(2)
    result = independent_heikin_ashi(bars)
    assert result[0] == (100.25, 100.125)
    assert result[1][0] == (result[0][0] + result[0][1]) / 2.0


def test_cme_equal_vwap_resets_at_session_open() -> None:
    bars = _bars(2)
    result = session_vwap(bars, "equal", "cme_open")
    assert result[0] == (101.0 + 99.0 + 100.5) / 3.0
    assert result[1] != result[0]
