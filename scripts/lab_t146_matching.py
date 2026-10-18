#!/usr/bin/env python3
"""Run the diagnostic T-146 movement matching study.

The study compares ATR-normalised price shapes in the unsealed HistData lake.
It is intentionally outside the Strategy Lab promotion path: it never opens the
sealed holdout, calls a broker, places an order, writes the gate or ledger, or
changes any cost configuration.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - optional desktop dependency
    np = None  # type: ignore[assignment]

try:
    from trader.data.resample import Candle
except ModuleNotFoundError:  # direct ``python scripts/lab_t146_matching.py`` execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from trader.data.resample import Candle

try:
    from lab_t146_stats import QueryRecord, evaluate_cluster, evaluate_group, timestamp_index
except ModuleNotFoundError:  # imported from the repository root by unit tests
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lab_t146_stats import QueryRecord, evaluate_cluster, evaluate_group, timestamp_index


END_HOLDOUT = datetime(2026, 7, 1, tzinfo=UTC)
IN_SAMPLE_START = datetime(2022, 1, 1, tzinfo=UTC)
OOS_START = datetime(2025, 1, 1, tzinfo=UTC)
INSTRUMENTS = ("NSXUSD", "XAUUSD")
TIMEFRAMES = (60, 5, 3, 1)
WINDOW_LENGTHS = (30, 60, 120)
HORIZONS = (6, 12, 30)
CLUSTER_COUNTS = (8, 16, 32)
DEFAULT_MAX_QUERIES = 2000
CORRELATION_PREFILTER = 200
MAX_LIBRARY_WINDOWS = 50000
MAX_OOS_WINDOWS = 20000
CLUSTER_SAMPLE_LIMIT = 3000
MEDOID_CANDIDATE_LIMIT = 64
MEDOID_REFERENCE_LIMIT = 128
ASSUMED_COSTS = {"NSXUSD": 4.0, "XAUUSD": 1.6}


@dataclass(frozen=True, slots=True)
class ShapeWindow:
    """One past-only, ATR-normalised movement window."""

    start: int
    end: int
    ts_start: datetime
    ts_end: datetime
    atr: float
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Match:
    window: ShapeWindow
    distance: float
    method: str


@dataclass(frozen=True, slots=True)
class ShapeCluster:
    """A deterministic, bounded k-medoids cluster of shape windows."""

    cluster_id: int
    medoid: ShapeWindow
    members: tuple[ShapeWindow, ...]


@dataclass(frozen=True, slots=True)
class MatchIndex:
    """Precomputed matrix for one fixed-length library.

    Every call in the OOS loop uses the same in-sample library.  Keeping its
    values in one NumPy matrix makes the correlation prefilter and correlation
    baseline a single vectorised operation instead of a Python loop over every
    library window.  The scalar path remains available when NumPy is absent.
    """

    candidates: tuple[ShapeWindow, ...]
    matrix: Any | None


def build_match_index(library: Sequence[ShapeWindow]) -> MatchIndex:
    """Build a reusable index for a fixed-length library."""
    candidates = tuple(library)
    matrix = None
    if np is not None and candidates and len({len(item.values) for item in candidates}) == 1:
        matrix = np.asarray([item.values for item in candidates], dtype=float)
    return MatchIndex(candidates=candidates, matrix=matrix)


def _finite(values: Iterable[float]) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def true_ranges(candles: Sequence[Candle]) -> tuple[float, ...]:
    """Return true ranges without looking beyond the supplied sequence."""
    result: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        if previous_close is None:
            result.append(float(candle.high - candle.low))
        else:
            result.append(
                max(
                    float(candle.high - candle.low),
                    abs(float(candle.high - previous_close)),
                    abs(float(candle.low - previous_close)),
                )
            )
        previous_close = float(candle.close)
    return tuple(result)


def atr_at(candles: Sequence[Candle], index: int, period: int = 14) -> float:
    """Calculate ATR immediately before ``index`` (no look-ahead)."""
    if index <= 0 or period <= 0 or index > len(candles):
        return 0.0
    ranges = true_ranges(candles[:index])
    sample = ranges[-period:]
    return mean(sample) if sample and _finite(sample) else 0.0


def normalise_shape(closes: Sequence[float], atr: float) -> tuple[float, ...]:
    """Express closes as movement from the first close in ATR units."""
    if not closes or not _finite(closes):
        raise ValueError("closes must be a non-empty finite sequence")
    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("ATR must be finite and positive")
    origin = float(closes[0])
    return tuple((float(value) - origin) / atr for value in closes)


def _daily_break_gap(previous: datetime, current: datetime, timeframe: int) -> bool:
    """Return whether an hourly bar step crosses the known HistData daily break.

    HistData's fixed-UTC feeds print the last hourly bar at 20:00 or 21:00 UTC
    and resume at 23:00 UTC (the exact break differs by symbol). For
    60-minute bars those non-trading hours are skipped trading-time intervals,
    so they must not make every 30/60/120-bar window unusable. Smaller
    timeframes keep the strict rule and never bridge a session break. Weekend
    gaps remain hard boundaries for every timeframe.
    """
    if timeframe < 60 or previous.date() != current.date():
        return False
    return (
        previous.hour in {20, 21}
        and current.hour == 23
        and current - previous in {timedelta(hours=2), timedelta(hours=3)}
    )


def _continuous(candles: Sequence[Candle], start: int, end: int, timeframe: int) -> bool:
    if end > len(candles) or end <= start:
        return False
    expected = timedelta(minutes=timeframe)
    return all(
        candles[index].ts_utc - candles[index - 1].ts_utc == expected
        or _daily_break_gap(candles[index - 1].ts_utc, candles[index].ts_utc, timeframe)
        for index in range(start + 1, end)
    )


def build_windows(
    candles: Sequence[Candle],
    *,
    start: datetime,
    end: datetime,
    timeframe: int,
    lengths: Sequence[int] = WINDOW_LENGTHS,
    stride_divisor: int = 4,
    atr_period: int = 14,
    max_windows: int | None = None,
) -> dict[int, tuple[ShapeWindow, ...]]:
    """Build windows wholly inside a date range, skipping data gaps."""
    if stride_divisor <= 0:
        raise ValueError("stride_divisor must be positive")
    selected = [candle for candle in candles if start <= candle.ts_utc < end]
    # The loader returns UTC candles, but keeping this explicit makes synthetic
    # test fixtures and direct callers safe.
    selected.sort(key=lambda candle: candle.ts_utc)
    ranges = true_ranges(selected)
    result: dict[int, tuple[ShapeWindow, ...]] = {}
    for length in lengths:
        if length <= 0:
            raise ValueError("window lengths must be positive")
        stride = max(1, length // stride_divisor)
        windows: list[ShapeWindow] = []
        indices = list(range(atr_period, len(selected) - length + 1, stride))
        if max_windows is not None and max_windows > 0 and len(indices) > max_windows:
            sample_stride = math.ceil(len(indices) / max_windows)
            indices = indices[::sample_stride][:max_windows]
        for start_index in indices:
            stop = start_index + length
            if not _continuous(selected, start_index, stop, timeframe):
                continue
            sample = ranges[max(0, start_index - atr_period) : start_index]
            atr = mean(sample) if sample and _finite(sample) else 0.0
            closes = [float(item.close) for item in selected[start_index:stop]]
            if atr <= 0 or not _finite(closes):
                continue
            windows.append(
                ShapeWindow(
                    start=start_index,
                    end=stop,
                    ts_start=selected[start_index].ts_utc,
                    ts_end=selected[stop - 1].ts_utc,
                    atr=atr,
                    values=normalise_shape(closes, atr),
                )
            )
        result[length] = tuple(windows)
    return result


def dtw_distance(left: Sequence[float], right: Sequence[float], band_ratio: float = 0.10) -> float:
    """Return band-limited, normalised dynamic time-warping distance."""
    if not left or not right or not _finite(left) or not _finite(right):
        raise ValueError("DTW inputs must be finite and non-empty")
    band = max(1, int(math.ceil(max(len(left), len(right)) * band_ratio)))
    previous: dict[int, float] = {0: 0.0}
    for i, value_left in enumerate(left, 1):
        current: dict[int, float] = {}
        low = max(1, i - band)
        high = min(len(right), i + band)
        for j in range(low, high + 1):
            cost = (float(value_left) - float(right[j - 1])) ** 2
            best = min(
                previous.get(j, math.inf),
                current.get(j - 1, math.inf),
                previous.get(j - 1, math.inf),
            )
            if math.isfinite(best):
                current[j] = cost + best
        previous = current
    value = previous.get(len(right), math.inf)
    return math.sqrt(value / max(len(left), len(right))) if math.isfinite(value) else math.inf


def lb_keogh_lower_bound(
    query: Sequence[float], candidate: Sequence[float], band_ratio: float = 0.10
) -> float:
    """Return the LB_Keogh lower bound used to skip hopeless DTW candidates."""
    if len(query) != len(candidate) or not query or not _finite(query) or not _finite(candidate):
        return math.inf
    radius = max(1, int(math.ceil(len(query) * band_ratio)))
    total = 0.0
    for index, value in enumerate(query):
        lower = min(candidate[max(0, index - radius) : min(len(candidate), index + radius + 1)])
        upper = max(candidate[max(0, index - radius) : min(len(candidate), index + radius + 1)])
        if value < lower:
            total += (value - lower) ** 2
        elif value > upper:
            total += (value - upper) ** 2
    return math.sqrt(total / len(query))


def correlation_distance(left: Sequence[float], right: Sequence[float]) -> float:
    """Return ``1 - Pearson r``; constant shapes are treated as maximally distant."""
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("correlation inputs must have equal length >= 2")
    left_mean, right_mean = mean(left), mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum((a - left_mean) ** 2 for a in left))
    right_norm = math.sqrt(sum((b - right_mean) ** 2 for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0 if tuple(left) == tuple(right) else 1.0
    correlation = max(-1.0, min(1.0, numerator / (left_norm * right_norm)))
    return 1.0 - correlation


def _non_overlapping(matches: Sequence[Match], length: int, limit: int) -> tuple[Match, ...]:
    chosen: list[Match] = []
    for match in sorted(matches, key=lambda item: (item.distance, item.window.start)):
        if all(abs(match.window.start - item.window.start) >= length for item in chosen):
            chosen.append(match)
        if len(chosen) >= limit:
            break
    return tuple(chosen)


def _sample_queries(
    queries: Sequence[ShapeWindow], max_queries: int | None
) -> tuple[tuple[ShapeWindow, ...], int]:
    """Select a deterministic every-mth query sample under the registered cap."""
    if max_queries is None or len(queries) <= max_queries:
        return tuple(queries), 1
    stride = max(1, math.ceil(len(queries) / max_queries))
    return tuple(queries[::stride][:max_queries]), stride


def find_matches(
    query: ShapeWindow,
    library: Sequence[ShapeWindow],
    *,
    method: str = "dtw",
    k: int = 20,
    correlation_prefilter: int = CORRELATION_PREFILTER,
    index: MatchIndex | None = None,
) -> tuple[Match, ...]:
    """Find top past-only matches and remove overlapping candidates.

    ``index`` enables the fast path used by the real-lake runner.  It computes
    correlation distances for a whole library matrix at once, while retaining
    the scalar implementation for callers without NumPy and for reference
    comparisons in the unit tests.
    """
    if method not in {"dtw", "correlation"}:
        raise ValueError("method must be dtw or correlation")
    if k <= 0:
        raise ValueError("k must be positive")
    candidates = tuple(index.candidates if index is not None else library)
    eligible_pairs = [
        (position, candidate)
        for position, candidate in enumerate(candidates)
        if candidate.ts_end < query.ts_start and len(candidate.values) == len(query.values)
    ]
    eligible = [candidate for _, candidate in eligible_pairs]
    if index is not None and index.matrix is not None and np is not None and eligible:
        positions = [position for position, _ in eligible_pairs]
        query_array = np.asarray(query.values, dtype=float)
        matrix = index.matrix[positions]
        centred = matrix - matrix.mean(axis=1, keepdims=True)
        query_centred = query_array - query_array.mean()
        denominator = np.linalg.norm(centred, axis=1) * np.linalg.norm(query_centred)
        with np.errstate(divide="ignore", invalid="ignore"):
            distances_array = 1.0 - (centred @ query_centred) / denominator
        equal_constant = np.all(matrix == query_array, axis=1)
        distances_array = np.where((denominator == 0) & equal_constant, 0.0, distances_array)
        distances_array = np.where((denominator == 0) & ~equal_constant, 1.0, distances_array)
        distances = [float(value) for value in distances_array]
        if method == "correlation":
            scored = [
                Match(candidate, distance, method)
                for candidate, distance in zip(eligible, distances, strict=True)
                if math.isfinite(distance)
            ]
            return _non_overlapping(scored, len(query.values), k)
        if method == "dtw" and correlation_prefilter > 0 and len(eligible) > correlation_prefilter:
            order = sorted(
                range(len(eligible)),
                key=lambda position: (distances[position], eligible[position].start),
            )[:correlation_prefilter]
            eligible = [eligible[position] for position in order]
    if method == "dtw" and correlation_prefilter > 0 and len(eligible) > correlation_prefilter:
        eligible.sort(key=lambda candidate: correlation_distance(query.values, candidate.values))
        eligible = eligible[:correlation_prefilter]
    scored: list[Match] = []
    retained_limit = max(k * 8, k)
    worst_bound = math.inf
    for candidate in eligible:
        if method == "dtw" and lb_keogh_lower_bound(query.values, candidate.values) >= worst_bound:
            continue
        distance = (
            dtw_distance(query.values, candidate.values)
            if method == "dtw"
            else correlation_distance(query.values, candidate.values)
        )
        if math.isfinite(distance):
            scored.append(Match(candidate, distance, method))
            if len(scored) >= retained_limit:
                scored.sort(key=lambda item: (item.distance, item.window.start))
                scored = scored[:retained_limit]
                worst_bound = scored[-1].distance
    return _non_overlapping(scored, len(query.values), k)


def cluster_windows(
    library: Sequence[ShapeWindow], *, k: int, max_windows: int = CLUSTER_SAMPLE_LIMIT
) -> tuple[ShapeCluster, ...]:
    """Build bounded deterministic k-medoids clusters for an overview."""
    if not library or k <= 0:
        return ()
    sample = tuple(library)
    if len(sample) > max_windows:
        sample = tuple(
            sample[index] for index in range(0, len(sample), max(1, len(sample) // max_windows))
        )[:max_windows]
    k = min(k, len(sample))
    medoids = [sample[index * len(sample) // k] for index in range(k)]
    for _ in range(2):
        buckets: list[list[ShapeWindow]] = [[] for _ in medoids]
        for item in sample:
            distances = [dtw_distance(item.values, medoid.values) for medoid in medoids]
            buckets[min(range(len(distances)), key=distances.__getitem__)].append(item)
        updated: list[ShapeWindow] = []
        for index, bucket in enumerate(buckets):
            if not bucket:
                updated.append(medoids[index])
                continue
            candidate_stride = max(1, math.ceil(len(bucket) / MEDOID_CANDIDATE_LIMIT))
            candidates = bucket[::candidate_stride][:MEDOID_CANDIDATE_LIMIT]
            reference_stride = max(1, math.ceil(len(bucket) / MEDOID_REFERENCE_LIMIT))
            references = bucket[::reference_stride][:MEDOID_REFERENCE_LIMIT]
            updated.append(
                min(
                    candidates,
                    key=lambda candidate: sum(
                        dtw_distance(candidate.values, other.values) for other in references
                    ),
                )
            )
        medoids = updated
    buckets = [[] for _ in medoids]
    for item in sample:
        distances = [dtw_distance(item.values, medoid.values) for medoid in medoids]
        buckets[min(range(len(distances)), key=distances.__getitem__)].append(item)
    return tuple(
        ShapeCluster(cluster_id=index, medoid=medoids[index], members=tuple(bucket))
        for index, bucket in enumerate(buckets)
        if bucket
    )


def random_control(
    query: ShapeWindow,
    library: Sequence[ShapeWindow],
    *,
    count: int,
    seed: int = 0,
) -> tuple[ShapeWindow, ...]:
    """Sample past windows with the same count and approximately the same time spread."""
    eligible = [item for item in library if item.ts_end < query.ts_start]
    if count <= 0 or not eligible:
        return ()
    rng = random.Random(seed)
    if len(eligible) <= count:
        return tuple(eligible)
    ordered = sorted(eligible, key=lambda item: item.ts_start)
    selected: list[ShapeWindow] = []
    for bucket in range(count):
        lower = bucket * len(ordered) // count
        upper = max(lower + 1, (bucket + 1) * len(ordered) // count)
        selected.append(rng.choice(ordered[lower:upper]))
    return tuple(sorted(selected, key=lambda item: item.ts_start))


def forward_move(
    candles: Sequence[Candle],
    window: ShapeWindow,
    *,
    candle_index: Mapping[datetime, int],
    horizon: int,
) -> dict[str, float | int | str | None]:
    """Measure the move after a matched window, with no future used in matching."""
    end_index = candle_index.get(window.ts_end)
    if end_index is None:
        return {"available": 0, "move_atr": None, "move_points": None, "direction": None}
    target_index = end_index + horizon
    if target_index >= len(candles):
        return {"available": 0, "move_atr": None, "move_points": None, "direction": None}
    start_price = float(candles[end_index].close)
    end_price = float(candles[target_index].close)
    move = end_price - start_price
    return {
        "available": 1,
        "move_atr": move / window.atr if window.atr else None,
        "move_points": move,
        "direction": "up" if move > 0 else "down" if move < 0 else "flat",
    }


def _candle_rows(candles: Sequence[Candle], indices: Sequence[int]) -> list[dict[str, Any]]:
    return [
        {
            "ts": candles[index].ts_utc.isoformat(),
            "open": candles[index].open,
            "high": candles[index].high,
            "low": candles[index].low,
            "close": candles[index].close,
        }
        for index in indices
    ]


def _window_indices(candles: Sequence[Candle], window: ShapeWindow) -> tuple[int, ...]:
    """Resolve window timestamps against the full series used for charting."""
    return tuple(
        index
        for index, candle in enumerate(candles)
        if window.ts_start <= candle.ts_utc <= window.ts_end
    )


def _save_shape_card(
    candles: Sequence[Candle],
    query: ShapeWindow,
    matches: Sequence[Match],
    controls: Sequence[ShapeWindow],
    path: Path,
) -> bool:
    """Write one owner-facing chart when matplotlib is available."""
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        from matplotlib import pyplot as plt
    except Exception:
        return False
    figure, axes = plt.subplots(2, 1, figsize=(12, 8), constrained_layout=True)
    query_indices = _window_indices(candles, query)
    axes[0].plot(
        [candles[index].ts_utc for index in query_indices],
        [candles[index].close for index in query_indices],
        label="query",
    )
    for match in matches[:5]:
        indices = _window_indices(candles, match.window)
        axes[0].plot(
            [candles[index].ts_utc for index in indices],
            [candles[index].close for index in indices],
            alpha=0.45,
            label=f"match {match.distance:.2f}",
        )
    axes[0].set_title("Query and top shape matches")
    axes[0].legend(loc="best", fontsize="small")
    for item in controls[:5]:
        indices = _window_indices(candles, item)
        axes[1].plot(
            [candles[index].ts_utc for index in indices],
            [candles[index].close for index in indices],
            alpha=0.5,
        )
    axes[1].set_title("Random control windows")
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=110)
    plt.close(figure)
    return True


def _save_overview_chart(
    candles: Sequence[Candle], clusters: Sequence[ShapeCluster], path: Path
) -> bool:
    """Write one full-history chart with cluster medoid markers."""
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        from matplotlib import pyplot as plt
    except Exception:
        return False
    if not candles or not clusters:
        return False
    figure, axis = plt.subplots(figsize=(14, 6), constrained_layout=True)
    axis.plot([item.ts_utc for item in candles], [item.close for item in candles], linewidth=0.6)
    for cluster in clusters:
        axis.scatter(
            cluster.medoid.ts_start,
            float(candles[cluster.medoid.start].close)
            if cluster.medoid.start < len(candles)
            else 0.0,
            s=18,
            label=f"cluster {cluster.cluster_id} (n={len(cluster.members)})",
        )
    axis.set_title("T-146 repeated-shape cluster medoids")
    axis.set_xlabel("UTC")
    axis.set_ylabel("close")
    if len(clusters) <= 16:
        axis.legend(loc="best", fontsize="x-small")
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=110)
    plt.close(figure)
    return True


def _memory_limit_bytes() -> int | None:
    try:
        value = Path("/sys/fs/cgroup/memory.max").read_text(encoding="ascii").strip()
        return None if value == "max" else int(value)
    except (OSError, ValueError):
        return None


def _load_histdata_candles(
    root: Path, instrument: str, timeframe: int, excluded: set[tuple[int, int]]
) -> tuple[Candle, ...]:
    """Load data lazily so synthetic helper tests do not need SQLAlchemy."""
    try:
        from trader.lab.runner import load_histdata_candles
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency error on bare desktops
        raise RuntimeError("Strategy Lab dependencies are required to run T-146") from exc
    return load_histdata_candles(root, instrument, timeframe, exclude_months=excluded)


def _cluster_evidence(
    candles: Sequence[Candle],
    candle_index: Mapping[datetime, int],
    library: Sequence[ShapeWindow],
    clusters: Sequence[ShapeCluster],
    queries: Sequence[ShapeWindow],
    *,
    instrument: str,
    length: int,
    cluster_count: int,
    trial_count: int,
) -> list[dict[str, Any]]:
    """Summarise in-sample and OOS outcomes for each shape cluster."""
    if not clusters:
        return []
    assignments: dict[int, list[ShapeWindow]] = {cluster.cluster_id: [] for cluster in clusters}
    for query in queries:
        cluster = min(
            clusters,
            key=lambda item: dtw_distance(query.values, item.medoid.values),
        )
        assignments[cluster.cluster_id].append(query)
    rows: list[dict[str, Any]] = []
    for cluster in clusters:
        members = cluster.members[:64]
        assigned = assignments[cluster.cluster_id]
        horizon_rows: list[dict[str, Any]] = []
        for horizon in HORIZONS:
            member_moves: list[float | None] = []
            control_moves: list[float | None] = []
            for member in members:
                controls = random_control(member, library, count=1, seed=member.start + horizon)
                member_row = forward_move(
                    candles, member, candle_index=candle_index, horizon=horizon
                )
                control_row = (
                    forward_move(candles, controls[0], candle_index=candle_index, horizon=horizon)
                    if controls
                    else {"available": 0, "move_points": None}
                )
                member_moves.append(member_row.get("move_points"))
                control_moves.append(control_row.get("move_points"))
            oos_realized = [
                forward_move(candles, query, candle_index=candle_index, horizon=horizon).get(
                    "move_points"
                )
                for query in assigned
            ]
            horizon_rows.append(
                {
                    "horizon": horizon,
                    "out_of_sample": evaluate_cluster(
                        member_moves,
                        control_moves,
                        oos_realized,
                        cost_points=ASSUMED_COSTS[instrument],
                        trial_count=trial_count,
                    ),
                    "in_sample_member_count": len(member_moves),
                    "oos_query_count": len(oos_realized),
                }
            )
        rows.append(
            {
                "length": length,
                "k": cluster_count,
                "cluster_id": cluster.cluster_id,
                "member_count": len(cluster.members),
                "oos_query_count": len(assigned),
                "medoid_start": cluster.medoid.ts_start.isoformat(),
                "horizons": horizon_rows,
            }
        )
    return rows


def run_group(
    candles: Sequence[Candle],
    *,
    instrument: str,
    timeframe: int,
    output_root: Path,
    holdout_end: datetime = END_HOLDOUT,
    max_queries: int | None = None,
    trial_count: int = 1,
    seed: int = 146,
    progress_path: Path | None = None,
) -> dict[str, Any]:
    """Run one symbol/timeframe group and return JSON-safe evidence."""
    started = time.perf_counter()
    queries_done = 0
    if progress_path is not None:
        _write_json(
            progress_path,
            {
                "status": "running",
                "instrument": instrument,
                "timeframe": timeframe,
                "queries_done": queries_done,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
    candle_index = timestamp_index(candles)
    in_sample = build_windows(
        candles,
        start=IN_SAMPLE_START,
        end=OOS_START,
        timeframe=timeframe,
        max_windows=MAX_LIBRARY_WINDOWS,
    )
    out_of_sample = build_windows(
        candles,
        start=OOS_START,
        end=holdout_end,
        timeframe=timeframe,
        max_windows=MAX_OOS_WINDOWS,
    )
    # Persist the window inventory before the expensive matching and cluster
    # loops begin.  The bounded T-153 parent may stop a child during those
    # loops; keeping these counts in the heartbeat prevents a partial
    # checkpoint from being misreported as an empty library.
    if progress_path is not None:
        _write_json(
            progress_path,
            {
                "status": "running",
                "instrument": instrument,
                "timeframe": timeframe,
                "queries_done": queries_done,
                "library_windows_by_length": {
                    str(length): len(value) for length, value in in_sample.items()
                },
                "oos_windows_by_length": {
                    str(length): len(value) for length, value in out_of_sample.items()
                },
                "library_windows": sum(len(value) for value in in_sample.values()),
                "oos_queries": sum(len(value) for value in out_of_sample.values()),
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
    results: list[dict[str, Any]] = []
    chart_dir = output_root / "t146_charts" / f"{instrument.lower()}_{timeframe}m"
    cluster_rows: list[dict[str, Any]] = []
    overview_clusters: list[ShapeCluster] = []
    query_stride = 1
    for length in WINDOW_LENGTHS:
        library = in_sample.get(length, ())
        match_index = build_match_index(library)
        queries, stride = _sample_queries(out_of_sample.get(length, ()), max_queries)
        query_stride = max(query_stride, stride)
        for method in ("dtw", "correlation"):
            records_by_horizon: dict[int, list[QueryRecord]] = {
                horizon: [] for horizon in HORIZONS
            }
            for query_number, query in enumerate(queries):
                # Matching and control selection do not depend on the forward horizon.
                # Compute them once and reuse them for all three horizons.
                matches = find_matches(
                    query,
                    library,
                    method=method,
                    k=20,
                    index=match_index,
                )
                controls = random_control(
                    query, library, count=len(matches), seed=seed + query_number
                )
                if query_number < 6 and method == "dtw":
                    _save_shape_card(
                        candles,
                        query,
                        matches,
                        controls,
                        chart_dir / f"query_{length}_{query_number:02d}.png",
                    )
                for horizon in HORIZONS:
                    matched_rows = [
                        forward_move(
                            candles,
                            match.window,
                            candle_index=candle_index,
                            horizon=horizon,
                        )
                        for match in matches
                    ]
                    control_rows = [
                        forward_move(
                            candles,
                            item,
                            candle_index=candle_index,
                            horizon=horizon,
                        )
                        for item in controls
                    ]
                    query_row = forward_move(
                        candles, query, candle_index=candle_index, horizon=horizon
                    )
                    records_by_horizon[horizon].append(
                        QueryRecord(
                            match_moves=tuple(row.get("move_points") for row in matched_rows),
                            control_moves=tuple(row.get("move_points") for row in control_rows),
                            realized=query_row.get("move_points"),
                        )
                    )
                # Keep the historical heartbeat unit: one query contributes one count
                # per evaluated horizon, so a complete group still reports 6x queries.
                queries_done += len(HORIZONS)
                if progress_path is not None:
                    _write_json(
                        progress_path,
                        {
                            "status": "running",
                            "instrument": instrument,
                            "timeframe": timeframe,
                            "queries_done": queries_done,
                            "elapsed_seconds": round(time.perf_counter() - started, 3),
                        },
                    )
            for horizon in HORIZONS:
                records = records_by_horizon[horizon]
                results.append(
                    {
                        "length": length,
                        "method": method,
                        "horizon": horizon,
                        "query_count": len(records),
                        "query_stride": stride,
                        **evaluate_group(
                            records,
                            cost_points=ASSUMED_COSTS[instrument],
                            trial_count=trial_count,
                        ),
                    }
                )
        if library:
            for cluster_count in CLUSTER_COUNTS:
                clusters = cluster_windows(library, k=cluster_count)
                cluster_rows.extend(
                    _cluster_evidence(
                        candles,
                        candle_index,
                        library,
                        clusters,
                        queries,
                        instrument=instrument,
                        length=length,
                        cluster_count=cluster_count,
                        trial_count=trial_count,
                    )
                )
                if cluster_count == CLUSTER_COUNTS[0]:
                    overview_clusters.extend(clusters)
    _save_overview_chart(candles, overview_clusters, chart_dir / "overview.png")
    evidence = {
        "instrument": instrument,
        "timeframe": timeframe,
        "library_windows": sum(len(value) for value in in_sample.values()),
        "oos_queries": sum(len(value) for value in out_of_sample.values()),
        "library_window_cap": MAX_LIBRARY_WINDOWS,
        "oos_window_cap": MAX_OOS_WINDOWS,
        "queries_evaluated": sum(
            len(_sample_queries(value, max_queries)[0]) for value in out_of_sample.values()
        ),
        "query_stride": query_stride,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "results": results,
        "clusters": cluster_rows,
        "charts_dir": str(chart_dir),
    }
    if progress_path is not None:
        _write_json(
            progress_path,
            {
                "status": "complete",
                "instrument": instrument,
                "timeframe": timeframe,
                "queries_done": queries_done,
                "elapsed_seconds": evidence["elapsed_seconds"],
            },
        )
    return evidence


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# T-146 movement matching report",
        "",
        "Diagnostic only. No broker, orders, paper trading, holdout, Gate 1, ledger "
        "or promotion activity.",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"In-sample: `{payload['split']['in_sample_start']}` to "
        f"`{payload['split']['in_sample_end']}`",
        f"Out-of-sample: `{payload['split']['oos_start']}` to `{payload['split']['holdout_end']}`",
        "",
        "Each result uses past-only ATR-normalised windows, DTW and a correlation baseline, "
        "with a random control matched for count and time spread.",
        "Net movement applies the cost to a stated trade direction; an adverse move is "
        "charged the cost rather than made less negative. OOS verdicts also require the "
        "query's own realized move to be positive after following the direction predicted "
        "by its past matches.",
        "",
        "## Groups",
        "",
    ]
    for group in payload["groups"]:
        lines.append(
            f"- **{group['instrument']} {group['timeframe']}m**: "
            f"{group['library_windows']} in-sample windows, {group['oos_queries']} OOS queries, "
            f"{group['queries_evaluated']} evaluated under the query cap, "
            f"{len(group['results'])} result rows in {group['elapsed_seconds']}s; "
            f"charts: `{group['charts_dir']}`"
        )
        for row in group["results"]:
            lines.append(
                "  - "
                f"length={row['length']} method={row['method']} horizon={row['horizon']}: "
                f"trades={row.get('trades')}, win_rate={row.get('win_rate')}, "
                f"mean_net_points={row.get('mean_net_points')}, "
                f"control_mean_net_points={row.get('control_mean_net_points')}, "
                f"edge_points={row.get('edge_points')}, sign_test_p={row.get('sign_test_p')}, "
                f"verdict={row.get('verdict')}"
            )
        verdicts = {row.get("verdict") for row in group["results"]}
        lines.append(
            "  Multiple-testing verdicts: "
            f"{', '.join(sorted(value for value in verdicts if value))}; "
            f"query stride={group['query_stride']}; Bonferroni alpha is recorded per row."
        )
    lines.extend(
        [
            "",
            "A movement is a candidate only when its out-of-sample net-of-cost result beats "
            "the matched random control after the pre-registered trial correction. This "
            "diagnostic does not promote a strategy.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--output", type=Path, default=Path("docs/evidence"))
    parser.add_argument("--holdout-end", type=str, default=END_HOLDOUT.isoformat())
    parser.add_argument("--instrument", choices=INSTRUMENTS)
    parser.add_argument("--timeframe", type=int, choices=TIMEFRAMES)
    parser.add_argument(
        "--progress-file",
        type=Path,
        help="write per-group progress for a bounded parent runner",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=DEFAULT_MAX_QUERIES,
        help="maximum OOS queries evaluated per window length (0 means all)",
    )
    parser.add_argument("--seed", type=int, default=146)
    args = parser.parse_args(argv)
    if (args.instrument is None) != (args.timeframe is None):
        parser.error("--instrument and --timeframe must be provided together")
    holdout_end = datetime.fromisoformat(args.holdout_end.replace("Z", "+00:00"))
    if holdout_end.tzinfo is None:
        holdout_end = holdout_end.replace(tzinfo=UTC)
    holdout_end = holdout_end.astimezone(UTC)
    if holdout_end <= OOS_START:
        raise SystemExit("holdout end must be after the OOS start")
    groups: list[dict[str, Any]] = []
    max_queries = None if args.max_queries == 0 else max(1, args.max_queries)
    matching_trials = len(INSTRUMENTS) * len(TIMEFRAMES) * len(WINDOW_LENGTHS) * 2 * len(HORIZONS)
    cluster_trials = (
        len(INSTRUMENTS)
        * len(TIMEFRAMES)
        * len(WINDOW_LENGTHS)
        * len(CLUSTER_COUNTS)
        * len(HORIZONS)
    )
    trial_count = matching_trials + cluster_trials
    # Exclude calendar months at and after the configured boundary before the
    # Parquet loader materialises rows. The holdout directory is never opened.
    excluded = {
        (year, month)
        for year in range(holdout_end.year, holdout_end.year + 20)
        for month in range(1, 13)
        if (year, month) >= (holdout_end.year, holdout_end.month)
    }
    selected_groups = (
        ((args.instrument, args.timeframe),)
        if args.instrument is not None and args.timeframe is not None
        else tuple(
            (instrument, timeframe) for instrument in INSTRUMENTS for timeframe in TIMEFRAMES
        )
    )
    for instrument, timeframe in selected_groups:
        candles = _load_histdata_candles(args.lake, instrument, 1, excluded)
        candles = tuple(item for item in candles if IN_SAMPLE_START <= item.ts_utc < holdout_end)
        # Keep 1m as the source and resample through the existing loader so
        # complete-bar handling remains identical to the Strategy Lab.
        series = (
            candles
            if timeframe == 1
            else _load_histdata_candles(args.lake, instrument, timeframe, excluded)
        )
        series = tuple(item for item in series if IN_SAMPLE_START <= item.ts_utc < holdout_end)
        group = run_group(
            series,
            instrument=instrument,
            timeframe=timeframe,
            output_root=args.output,
            holdout_end=holdout_end,
            max_queries=max_queries,
            trial_count=trial_count,
            seed=args.seed,
            progress_path=args.progress_file,
        )
        groups.append(group)
        if args.instrument is not None:
            _write_json(
                args.output / f"t146_group_{instrument.lower()}_{timeframe}m.json",
                group,
            )
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "study": "T-146 movement matching",
        "split": {
            "in_sample_start": IN_SAMPLE_START.isoformat(),
            "in_sample_end": OOS_START.isoformat(),
            "oos_start": OOS_START.isoformat(),
            "holdout_end": holdout_end.isoformat(),
        },
        "parameters": {
            "window_lengths": WINDOW_LENGTHS,
            "horizons": HORIZONS,
            "stride": "N/4",
            "methods": ["dtw", "correlation"],
            "top_k": 20,
            "cluster_counts": CLUSTER_COUNTS,
            "max_queries": max_queries,
            "query_sampling": "every m-th OOS window, deterministic",
            "correlation_prefilter": CORRELATION_PREFILTER,
            "library_window_cap": MAX_LIBRARY_WINDOWS,
            "oos_window_cap": MAX_OOS_WINDOWS,
            "run_order": TIMEFRAMES,
            "assumed_cost_points": ASSUMED_COSTS,
            "sealed_holdout_read": False,
        },
        "system": {"cpu_count": os.cpu_count(), "memory_limit_bytes": _memory_limit_bytes()},
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "paper_trading": False,
            "gate1_evaluated": False,
            "ledger_writes": 0,
            "promotion_writes": 0,
        },
        "trial_count": trial_count,
        "groups": groups,
    }
    _write_json(args.output / "t146_matching_summary.json", payload)
    (args.output / "t146_matching_report.md").write_text(render_report(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "groups": len(groups),
                "trial_count": payload["trial_count"],
                "sealed_holdout_read": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
