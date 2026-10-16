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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any

try:
    from trader.data.resample import Candle
except ModuleNotFoundError:  # direct ``python scripts/lab_t146_matching.py`` execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from trader.data.resample import Candle


END_HOLDOUT = datetime(2026, 7, 1, tzinfo=UTC)
IN_SAMPLE_START = datetime(2022, 1, 1, tzinfo=UTC)
OOS_START = datetime(2025, 1, 1, tzinfo=UTC)
INSTRUMENTS = ("NSXUSD", "XAUUSD")
TIMEFRAMES = (1, 3, 5, 60)
WINDOW_LENGTHS = (30, 60, 120)
HORIZONS = (6, 12, 30)
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


def _continuous(candles: Sequence[Candle], start: int, end: int, timeframe: int) -> bool:
    if end > len(candles) or end <= start:
        return False
    expected = timedelta(minutes=timeframe)
    return all(
        candles[index].ts_utc - candles[index - 1].ts_utc == expected
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
) -> dict[int, tuple[ShapeWindow, ...]]:
    """Build windows wholly inside a date range, skipping data gaps."""
    if stride_divisor <= 0:
        raise ValueError("stride_divisor must be positive")
    selected = [
        candle
        for candle in candles
        if start <= candle.ts_utc < end
    ]
    # The loader returns UTC candles, but keeping this explicit makes synthetic
    # test fixtures and direct callers safe.
    selected.sort(key=lambda candle: candle.ts_utc)
    result: dict[int, tuple[ShapeWindow, ...]] = {}
    for length in lengths:
        if length <= 0:
            raise ValueError("window lengths must be positive")
        stride = max(1, length // stride_divisor)
        windows: list[ShapeWindow] = []
        for start_index in range(atr_period, len(selected) - length + 1, stride):
            stop = start_index + length
            if not _continuous(selected, start_index, stop, timeframe):
                continue
            atr = atr_at(selected, start_index, atr_period)
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


def find_matches(
    query: ShapeWindow,
    library: Sequence[ShapeWindow],
    *,
    method: str = "dtw",
    k: int = 20,
) -> tuple[Match, ...]:
    """Find top past-only matches and remove overlapping candidates."""
    if method not in {"dtw", "correlation"}:
        raise ValueError("method must be dtw or correlation")
    if k <= 0:
        raise ValueError("k must be positive")
    scored: list[Match] = []
    for candidate in library:
        if candidate.ts_end >= query.ts_start or len(candidate.values) != len(query.values):
            continue
        distance = (
            dtw_distance(query.values, candidate.values)
            if method == "dtw"
            else correlation_distance(query.values, candidate.values)
        )
        if math.isfinite(distance):
            scored.append(Match(candidate, distance, method))
    return _non_overlapping(scored, len(query.values), k)


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
    horizon: int,
) -> dict[str, float | int | str | None]:
    """Measure the move after a matched window, with no future used in matching."""
    target_index = window.end + horizon - 1
    if target_index >= len(candles):
        return {"available": 0, "move_atr": None, "move_points": None, "direction": None}
    start_price = float(candles[window.end - 1].close)
    end_price = float(candles[target_index].close)
    move = end_price - start_price
    return {
        "available": 1,
        "move_atr": move / window.atr if window.atr else None,
        "move_points": move,
        "direction": "up" if move > 0 else "down" if move < 0 else "flat",
    }


def summarise_moves(
    rows: Sequence[dict[str, Any]],
    *,
    cost_points: float,
) -> dict[str, Any]:
    available = [row for row in rows if row.get("available")]
    values = [float(row["move_points"]) for row in available]
    net = [value - cost_points if value > 0 else value + cost_points for value in values]
    return {
        "count": len(available),
        "up_share": (sum(row.get("direction") == "up" for row in available) / len(available))
        if available
        else None,
        "mean_move_points": mean(values) if values else None,
        "median_move_points": median(values) if values else None,
        "mean_net_points": mean(net) if net else None,
        "assumed_round_trip_cost_points": cost_points,
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
    query_indices = range(query.start, query.end)
    axes[0].plot(
        [candles[index].ts_utc for index in query_indices],
        [candles[index].close for index in query_indices],
        label="query",
    )
    for match in matches[:5]:
        indices = range(match.window.start, match.window.end)
        axes[0].plot(
            [candles[index].ts_utc for index in indices],
            [candles[index].close for index in indices],
            alpha=0.45,
            label=f"match {match.distance:.2f}",
        )
    axes[0].set_title("Query and top shape matches")
    axes[0].legend(loc="best", fontsize="small")
    for item in controls[:5]:
        indices = range(item.start, item.end)
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


def run_group(
    candles: Sequence[Candle],
    *,
    instrument: str,
    timeframe: int,
    output_root: Path,
    holdout_end: datetime = END_HOLDOUT,
    max_queries: int | None = None,
    seed: int = 146,
) -> dict[str, Any]:
    """Run one symbol/timeframe group and return JSON-safe evidence."""
    in_sample = build_windows(
        candles,
        start=IN_SAMPLE_START,
        end=OOS_START,
        timeframe=timeframe,
    )
    out_of_sample = build_windows(
        candles,
        start=OOS_START,
        end=holdout_end,
        timeframe=timeframe,
    )
    results: list[dict[str, Any]] = []
    chart_dir = output_root / "t146_charts" / f"{instrument.lower()}_{timeframe}m"
    for length in WINDOW_LENGTHS:
        library = in_sample.get(length, ())
        queries = list(out_of_sample.get(length, ()))
        if max_queries is not None:
            queries = queries[:max_queries]
        for query_number, query in enumerate(queries):
            for method in ("dtw", "correlation"):
                matches = find_matches(query, library, method=method, k=20)
                controls = random_control(
                    query, library, count=len(matches), seed=seed + query_number
                )
                for horizon in HORIZONS:
                    matched_rows = [
                        forward_move(candles, match.window, horizon=horizon) for match in matches
                    ]
                    control_rows = [
                        forward_move(candles, item, horizon=horizon) for item in controls
                    ]
                    results.append(
                        {
                            "length": length,
                            "query": query_number,
                            "query_start": query.ts_start.isoformat(),
                            "method": method,
                            "horizon": horizon,
                            "matches": summarise_moves(
                                matched_rows, cost_points=ASSUMED_COSTS[instrument]
                            ),
                            "random_control": summarise_moves(
                                control_rows, cost_points=ASSUMED_COSTS[instrument]
                            ),
                        }
                    )
                if query_number < 6 and method == "dtw":
                    _save_shape_card(
                        candles,
                        query,
                        matches,
                        controls,
                        chart_dir / f"query_{length}_{query_number:02d}.png",
                    )
    return {
        "instrument": instrument,
        "timeframe": timeframe,
        "library_windows": sum(len(value) for value in in_sample.values()),
        "oos_queries": sum(len(value) for value in out_of_sample.values()),
        "results": results,
        "charts_dir": str(chart_dir),
    }


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
        "",
        "## Groups",
        "",
    ]
    for group in payload["groups"]:
        lines.append(
            f"- **{group['instrument']} {group['timeframe']}m**: "
            f"{group['library_windows']} in-sample windows, {group['oos_queries']} OOS queries, "
            f"{len(group['results'])} result rows; charts: `{group['charts_dir']}`"
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
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--seed", type=int, default=146)
    args = parser.parse_args(argv)
    holdout_end = datetime.fromisoformat(args.holdout_end.replace("Z", "+00:00"))
    if holdout_end.tzinfo is None:
        holdout_end = holdout_end.replace(tzinfo=UTC)
    holdout_end = holdout_end.astimezone(UTC)
    if holdout_end <= OOS_START:
        raise SystemExit("holdout end must be after the OOS start")
    groups: list[dict[str, Any]] = []
    # Exclude calendar months at and after the configured boundary before the
    # Parquet loader materialises rows. The holdout directory is never opened.
    excluded = {
        (year, month)
        for year in range(holdout_end.year, holdout_end.year + 20)
        for month in range(1, 13)
        if (year, month) >= (holdout_end.year, holdout_end.month)
    }
    for instrument in INSTRUMENTS:
        candles = _load_histdata_candles(args.lake, instrument, 1, excluded)
        candles = tuple(item for item in candles if IN_SAMPLE_START <= item.ts_utc < holdout_end)
        for timeframe in TIMEFRAMES:
            # Keep 1m as the source and resample through the existing loader so
            # complete-bar handling remains identical to the Strategy Lab.
            series = candles if timeframe == 1 else _load_histdata_candles(
                args.lake, instrument, timeframe, excluded
            )
            series = tuple(item for item in series if IN_SAMPLE_START <= item.ts_utc < holdout_end)
            groups.append(
                run_group(
                    series,
                    instrument=instrument,
                    timeframe=timeframe,
                    output_root=args.output,
                    holdout_end=holdout_end,
                    max_queries=args.max_queries,
                    seed=args.seed,
                )
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
        "trial_count": len(groups) * len(WINDOW_LENGTHS) * 2 * len(HORIZONS),
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
