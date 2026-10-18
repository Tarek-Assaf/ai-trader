"""Import-only and synthetic unit coverage for T-146 matching helpers."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def load_script():
    spec = importlib.util.spec_from_file_location(
        "t146_matching", ROOT / "scripts/lab_t146_matching.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def window(module, start: int, values: tuple[float, ...], *, day: int = 1):
    stamp = datetime(2024, 1, day, tzinfo=UTC)
    return module.ShapeWindow(
        start=start,
        end=start + len(values),
        ts_start=stamp + timedelta(minutes=start),
        ts_end=stamp + timedelta(minutes=start + len(values) - 1),
        atr=1.0,
        values=values,
    )


def candles(module, stamps: tuple[datetime, ...]):
    return tuple(
        module.Candle(
            ts_utc=stamp,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
        )
        for stamp in stamps
    )


def test_dtw_identical_windows_is_zero() -> None:
    module = load_script()
    assert module.dtw_distance((0.0, 1.0, 2.0), (0.0, 1.0, 2.0)) == 0.0


def test_atr_normalisation_is_scale_invariant() -> None:
    module = load_script()
    shape = (100.0, 101.0, 99.5, 102.0)
    scaled = tuple(value * 10 for value in shape)
    assert module.normalise_shape(shape, 2.0) == module.normalise_shape(scaled, 20.0)


def test_continuity_accepts_in_session_minute_bars() -> None:
    module = load_script()
    start = datetime(2024, 1, 2, 14, 0, tzinfo=UTC)
    series = candles(module, tuple(start + timedelta(minutes=index) for index in range(5)))
    assert module._continuous(series, 0, len(series), 1)


def test_continuity_rejects_weekend_gap() -> None:
    module = load_script()
    series = candles(
        module,
        (
            datetime(2024, 1, 5, 23, 59, tzinfo=UTC),
            datetime(2024, 1, 8, 0, 0, tzinfo=UTC),
        ),
    )
    assert not module._continuous(series, 0, len(series), 1)


def test_continuity_rejects_instrument_session_gap_without_fill() -> None:
    module = load_script()
    series = candles(
        module,
        (
            datetime(2024, 1, 2, 16, 59, tzinfo=UTC),
            datetime(2024, 1, 2, 18, 0, tzinfo=UTC),
        ),
    )
    assert not module._continuous(series, 0, len(series), 1)


def test_continuity_skips_known_daily_break_for_hourly_bars() -> None:
    module = load_script()
    series = candles(
        module,
        (
            datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
            datetime(2024, 1, 2, 23, 0, tzinfo=UTC),
        ),
    )
    assert module._continuous(series, 0, len(series), 60)
    assert not module._continuous(series, 0, len(series), 5)


def test_matches_are_strictly_past_only() -> None:
    module = load_script()
    query = window(module, 100, (0.0, 1.0, 0.0))
    past = window(module, 1, (0.0, 1.0, 0.0))
    future = window(module, 200, (0.0, 1.0, 0.0))
    matches = module.find_matches(query, (past, future), method="dtw", k=20)
    assert [item.window.start for item in matches] == [past.start]


def test_overlap_removal_keeps_one_match_per_window_length() -> None:
    module = load_script()
    query = window(module, 100, (0.0, 1.0, 0.0))
    first = window(module, 1, (0.0, 1.0, 0.0))
    overlapping = window(module, 2, (0.0, 1.1, 0.0))
    distant = window(module, 10, (0.0, 1.2, 0.0))
    matches = module.find_matches(query, (first, overlapping, distant), method="dtw", k=20)
    assert [item.window.start for item in matches] == [first.start, distant.start]


def test_random_control_preserves_count_and_time_spread() -> None:
    module = load_script()
    query = window(module, 200, (0.0, 1.0, 0.0), day=20)
    library = tuple(window(module, index, (0.0, 1.0, 0.0), day=index + 1) for index in range(10))
    controls = module.random_control(query, library, count=4, seed=4)
    assert len(controls) == 4
    assert controls[0].ts_start >= library[0].ts_start
    assert controls[-1].ts_start <= library[-1].ts_start
    assert controls[-1].ts_start > controls[0].ts_start


def test_repeated_shape_beats_white_noise() -> None:
    module = load_script()
    query = window(module, 100, (0.0, 1.0, 0.5, 1.5))
    repeated = window(module, 1, (0.0, 1.0, 0.5, 1.5))
    noise = window(module, 20, (0.0, -0.4, 0.8, -0.2))
    matches = module.find_matches(query, (repeated, noise), method="dtw", k=2)
    assert matches[0].window.start == repeated.start
    assert matches[0].distance < matches[1].distance


def test_vectorized_matcher_matches_scalar_scores() -> None:
    module = load_script()
    query = window(module, 100, (0.0, 1.0, 0.5, 1.5))
    library = tuple(
        window(module, index, values)
        for index, values in enumerate(
            (
                (0.0, 1.0, 0.5, 1.5),
                (0.0, 0.9, 0.4, 1.3),
                (0.0, -0.4, 0.8, -0.2),
                (0.0, 0.2, 0.1, 0.4),
                (0.0, 1.5, 0.2, 0.8),
            ),
            start=1,
        )
    )
    index = module.build_match_index(library)
    for method in ("dtw", "correlation"):
        scalar = module.find_matches(
            query, library, method=method, k=3, correlation_prefilter=3
        )
        vectorized = module.find_matches(
            query, library, method=method, k=3, correlation_prefilter=3, index=index
        )
        assert [item.window.start for item in vectorized] == [item.window.start for item in scalar]
        assert [item.distance for item in vectorized] == pytest.approx(
            [item.distance for item in scalar]
        )


def test_query_sampling_is_bounded_and_deterministic() -> None:
    module = load_script()
    queries = tuple(window(module, index, (0.0, 1.0, 0.0)) for index in range(10))
    sampled, stride = module._sample_queries(queries, 3)
    assert stride == 4
    assert [item.start for item in sampled] == [0, 4, 8]


def test_matching_imports_reference_statistics() -> None:
    module = load_script()
    assert module.evaluate_group is not None
    assert module.evaluate_cluster is not None
    assert module.QueryRecord is not None


def test_forward_move_uses_timestamp_index_result() -> None:
    module = load_script()
    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles = tuple(
        type("CandleStub", (), {"ts_utc": start + timedelta(minutes=i), "close": float(i)})()
        for i in range(6)
    )
    query = window(module, 0, (0.0, 1.0), day=1)
    query = module.ShapeWindow(
        start=0,
        end=2,
        ts_start=candles[0].ts_utc,
        ts_end=candles[1].ts_utc,
        atr=1.0,
        values=(0.0, 1.0),
    )
    indexed = module.forward_move(
        candles, query, candle_index=module.timestamp_index(candles), horizon=2
    )
    linear_end = next(i for i, candle in enumerate(candles) if candle.ts_utc == query.ts_end)
    assert indexed["move_points"] == candles[linear_end + 2].close - candles[linear_end].close


def test_report_lists_required_group_statistics() -> None:
    module = load_script()
    report = module.render_report(
        {
            "generated_at": "now",
            "split": {
                "in_sample_start": "a",
                "in_sample_end": "b",
                "oos_start": "b",
                "holdout_end": "c",
            },
            "groups": [
                {
                    "instrument": "TEST",
                    "timeframe": 1,
                    "library_windows": 1,
                    "oos_queries": 1,
                    "queries_evaluated": 1,
                    "results": [
                        {
                            "length": 30,
                            "method": "dtw",
                            "horizon": 6,
                            "trades": 30,
                            "win_rate": 0.5,
                            "mean_net_points": 1.0,
                            "control_mean_net_points": 0.5,
                            "edge_points": 0.5,
                            "sign_test_p": 0.01,
                            "verdict": "FOUND",
                        }
                    ],
                    "elapsed_seconds": 0.1,
                    "charts_dir": "charts",
                    "query_stride": 1,
                }
            ],
        }
    )
    for field in (
        "trades=30",
        "win_rate=0.5",
        "mean_net_points=1.0",
        "control_mean_net_points=0.5",
        "edge_points=0.5",
        "sign_test_p=0.01",
        "verdict=FOUND",
    ):
        assert field in report


def test_cluster_assignment_keeps_medoid_members() -> None:
    module = load_script()
    library = tuple(
        window(module, index, (0.0, 1.0, 0.0) if index < 4 else (0.0, -1.0, 0.0))
        for index in range(8)
    )
    clusters = module.cluster_windows(library, k=2, max_windows=8)
    assert len(clusters) == 2
    assert sum(len(cluster.members) for cluster in clusters) == len(library)
    assert all(cluster.medoid in cluster.members for cluster in clusters)
