from __future__ import annotations

from datetime import UTC, datetime

from scripts.lab_t164_clock_price_alignment import _alignment_for_values, _basis

from trader.data.resample import Candle


def test_alignment_removes_constant_basis() -> None:
    offset, residual = _alignment_for_values((100.0, 101.0, 102.0), (98.0, 99.0, 100.0))
    assert offset == 2.0
    assert residual == 0.0


def test_price_basis_values() -> None:
    bar = Candle(
        datetime(2024, 1, 1, tzinfo=UTC), 10.0, 13.0, 9.0, 12.0, 0.0, ("NAS100", "mid"), True, 1
    )
    assert _basis(bar, "open") == 10.0
    assert _basis(bar, "close") == 12.0
    assert _basis(bar, "hlc3") == (13.0 + 9.0 + 12.0) / 3.0
