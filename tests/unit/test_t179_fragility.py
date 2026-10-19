import random
from dataclasses import dataclass

from scripts.lab_t179_fragility import (
    new_signal_count,
    percentile,
    perturb_bar,
    same_direction_count,
)


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float


def test_perturbation_is_deterministic_and_repairs_ohlc():
    bar = Bar(open=100.0, high=102.0, low=98.0, close=101.0)
    first = perturb_bar(bar, 1.0, random.Random(179))
    second = perturb_bar(bar, 1.0, random.Random(179))
    assert first == second
    assert first.high >= max(first.open, first.close, first.low)
    assert first.low <= min(first.open, first.close, first.high)


def test_signal_matching_and_percentile():
    baseline = [{"index": 10, "direction": "long"}]
    candidate = [{"index": 12, "direction": "long"}, {"index": 50, "direction": "short"}]
    assert same_direction_count(baseline, candidate, 3) == 1
    assert new_signal_count(baseline, candidate) == 1
    assert percentile([0, 10, 20, 30], 0.5) == 15
