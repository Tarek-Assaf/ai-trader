"""Regression tests for the exact one-sided sign-test p-value."""

from __future__ import annotations

import math
from types import SimpleNamespace

from trader.lab.metrics import _sign_test_p_value


def _trades(values):
    return [SimpleNamespace(net_pnl=value) for value in values]


def _reference(values) -> float:
    kept = [value for value in values if value != 0]
    wins = sum(value > 0 for value in kept)
    count = len(kept)
    return min(1.0, sum(math.comb(count, i) for i in range(wins, count + 1)) / (2.0**count))


def test_sign_test_matches_reference_for_small_samples() -> None:
    for values in ([1.0], [1.0, -1.0, 2.0], [1.0] * 12 + [-1.0] * 8, [-1.0] * 10):
        assert abs(_sign_test_p_value(_trades(values)) - _reference(values)) < 1e-12


def test_sign_test_does_not_overflow_with_thousands_of_trades() -> None:
    balanced = [1.0 if index % 2 else -1.0 for index in range(5000)]
    value = _sign_test_p_value(_trades(balanced))
    assert 0.0 <= value <= 1.0
    assert abs(value - 0.5) < 0.05
    assert _sign_test_p_value(_trades([1.0] * 3000)) == 0.0
    assert _sign_test_p_value(_trades([-1.0] * 3000)) == 1.0
    assert _sign_test_p_value(_trades([0.0, 0.0])) == 1.0
