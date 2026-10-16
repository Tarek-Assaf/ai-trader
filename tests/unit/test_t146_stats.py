"""Tests for the T-146 reference statistics (V-381)."""

from __future__ import annotations

import importlib.util
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("t146_stats", ROOT / "scripts/lab_t146_stats.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_direction_uses_median_not_mean() -> None:
    m = load()
    assert m.direction_from_moves([1.0, 1.0, -100.0]) == 1  # mean is negative
    assert m.direction_from_moves([-1.0, -1.0, 100.0]) == -1
    assert m.direction_from_moves([0.0, 0.0, 5.0]) == 0
    assert m.direction_from_moves([]) == 0
    assert m.direction_from_moves([None, 2.0]) == 1


def test_losing_short_is_not_improved_by_cost() -> None:
    m = load()
    # short, market rose 10 points, cost 4: net = -10 - 4
    assert m.trade_net(-1, 10.0, 4.0) == -14.0
    # long that lost 10: -10 - 4, cost never shrinks a loss
    assert m.trade_net(1, -10.0, 4.0) == -14.0
    assert m.trade_net(-1, -10.0, 4.0) == 6.0  # winning short
    assert m.trade_net(0, 10.0, 4.0) is None  # flat = no trade
    assert m.trade_net(1, None, 4.0) is None


def test_planted_shape_found_out_of_sample_and_noise_not_found() -> None:
    m = load()
    rng = random.Random(7)
    planted, noise = [], []
    for _ in range(200):
        # matches were followed by a rise; the query really rose ~10 points
        planted.append(
            m.QueryRecord(
                match_moves=tuple(8 + rng.uniform(-2, 2) for _ in range(20)),
                control_moves=tuple(rng.uniform(-10, 10) for _ in range(20)),
                realized=10 + rng.uniform(-3, 3),
            )
        )
        # pure noise: matches, controls and the query are all unrelated
        noise.append(
            m.QueryRecord(
                match_moves=tuple(rng.uniform(-10, 10) for _ in range(20)),
                control_moves=tuple(rng.uniform(-10, 10) for _ in range(20)),
                realized=rng.uniform(-10, 10),
            )
        )
    found = m.evaluate_group(planted, cost_points=1.0, trial_count=24)
    assert found["verdict"] == "FOUND"
    assert found["trades"] == 200 and found["mean_net_points"] > 5
    assert found["edge_points"] > 0
    rejected = m.evaluate_group(noise, cost_points=1.0, trial_count=24)
    assert rejected["verdict"] == "NOT_FOUND"


def test_past_only_pattern_does_not_count_without_query_outcome() -> None:
    m = load()
    # matches say "up" but the queries really fell: must not be FOUND
    records = [m.QueryRecord((5.0,) * 10, (0.0,) * 10, -5.0) for _ in range(100)]
    result = m.evaluate_group(records, cost_points=1.0, trial_count=1)
    assert result["verdict"] == "NOT_FOUND"
    assert result["mean_net_points"] == -6.0


def test_too_few_trades_is_not_found() -> None:
    m = load()
    records = [m.QueryRecord((5.0,) * 5, (-5.0,) * 5, 9.0) for _ in range(10)]
    result = m.evaluate_group(records, cost_points=1.0, trial_count=1)
    assert result["trades"] == 10
    assert result["verdict"] == "NOT_FOUND"


def test_bonferroni_uses_full_trial_count() -> None:
    m = load()
    records = [m.QueryRecord((5.0,) * 5, (-5.0,) * 5, 3.0) for _ in range(40)]
    assert m.evaluate_group(records, cost_points=1.0, trial_count=1)["verdict"] == "FOUND"
    result = m.evaluate_group(records, cost_points=1.0, trial_count=10**12)
    assert result["verdict"] == "NOT_FOUND"
    assert result["bonferroni_alpha"] == 0.05 / 10**12


def test_cluster_direction_comes_from_in_sample_members() -> None:
    m = load()
    result = m.evaluate_cluster(
        member_moves=[4.0, 5.0, 6.0],
        control_moves=[-4.0, -5.0, -6.0],
        oos_realized=[5.0] * 40,
        cost_points=1.0,
        trial_count=1,
    )
    assert result["trades"] == 40 and result["mean_net_points"] == 4.0
    assert result["verdict"] == "FOUND"


def test_timestamp_index_equals_linear_scan() -> None:
    m = load()
    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles = [SimpleNamespace(ts_utc=start + timedelta(minutes=i)) for i in range(50)]
    index = m.timestamp_index(candles)
    for probe in (candles[0].ts_utc, candles[17].ts_utc, candles[49].ts_utc):
        linear = next(i for i, c in enumerate(candles) if c.ts_utc == probe)
        assert index[probe] == linear
