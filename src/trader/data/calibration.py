"""Basis, spread and timezone calibration for T-017.

The module is usable without a broker connection. It accepts row iterables so a
Parquet scanner can stream large tick input, keeps bounded reservoirs for
percentile estimates, and never writes PaperBroker costs without enough data.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import correlation, median
from typing import Any

import yaml

from trader.data.importers.histdata_ticks import iter_histdata_tick_rows
from trader.data.market_hours import MarketHours

try:
    import pyarrow.dataset as ds  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    ds = None

DEFAULT_RESERVOIR_SIZE = 100_000


@dataclass(frozen=True, slots=True)
class Distribution:
    count: int
    median: float | None
    p90: float | None
    p99: float | None


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    instrument: str
    basis: Distribution
    histdata_spread: Distribution
    capital_spread: Distribution
    best_lag_hours: int | None
    lag_correlations: Mapping[int, float]
    expected_lag_hours: int
    timezone_ok: bool
    session_stats: Mapping[str, Mapping[str, Distribution]]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["session_stats"] = {
            session: {name: asdict(stats) for name, stats in values.items()}
            for session, values in self.session_stats.items()
        }
        value["warnings"] = list(self.warnings)
        return value


class _Reservoir:
    """Bounded-memory, deterministic reservoir for percentile estimates."""

    def __init__(self, limit: int = DEFAULT_RESERVOIR_SIZE) -> None:
        if limit < 1:
            raise ValueError("reservoir limit must be positive")
        self.count = 0
        self.values: list[float] = []
        self._limit = limit
        self._random = random.Random(0)

    def add(self, value: float) -> None:
        self.count += 1
        if len(self.values) < self._limit:
            self.values.append(value)
            return
        slot = self._random.randrange(self.count)
        if slot < self._limit:
            self.values[slot] = value

    def distribution(self) -> Distribution:
        return _distribution(self.values, count=self.count)


def _timestamp(row: Mapping[str, Any]) -> datetime:
    value = row.get("ts_utc", row.get("timestamp"))
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("calibration timestamps must be timezone-aware")
    return value.astimezone(UTC).replace(second=0, microsecond=0)


def _value(row: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        value = row.get(name)
        if value is not None:
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("calibration values must be finite")
            return number
    return None


def _distribution(values: Iterable[float], *, count: int | None = None) -> Distribution:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return Distribution(0, None, None, None)

    def percentile(rank: float) -> float:
        index = (len(ordered) - 1) * rank
        lower, upper = math.floor(index), math.ceil(index)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)

    return Distribution(
        len(ordered) if count is None else count,
        median(ordered),
        percentile(0.90),
        percentile(0.99),
    )


def _default_session(stamp: datetime) -> str:
    """Fixture fallback; production should pass broker ``MarketHours``."""
    hour = stamp.hour
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 13:
        return "london"
    if 13 <= hour < 22:
        return "new_york"
    return "closed"


def _market_session(schedule: MarketHours, stamp: datetime) -> str:
    if schedule.is_open(stamp):
        return "open"
    if schedule.is_break(stamp):
        return "break"
    return "closed"


def _minute(value: str) -> int:
    hour_text, minute_text = value.split(":", maxsplit=1)
    hour, minute = int(hour_text), int(minute_text)
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("session window is outside a UTC day")
    return hour * 60 + minute


def load_session_windows(path: str | Path | None = None) -> dict[str, tuple[int, int]]:
    """Load the named UTC reporting windows shared by calibration runs."""
    config_path = (
        Path(path)
        if path is not None
        else Path(__file__).parents[3] / "config" / "sessions.yaml"
    )
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("sessions config is unreadable or malformed") from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("sessions"), Mapping):
        raise ValueError("sessions config must contain a sessions mapping")
    windows: dict[str, tuple[int, int]] = {}
    for name, raw in payload["sessions"].items():
        if not isinstance(name, str) or not isinstance(raw, Mapping):
            raise ValueError("session config entries are malformed")
        start, end = raw.get("start"), raw.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError(f"session {name} needs start and end")
        start_minute, end_minute = _minute(start), _minute(end)
        if start_minute >= end_minute:
            raise ValueError(f"session {name} must not cross midnight")
        windows[name] = (start_minute, end_minute)
    return windows


def _named_session(stamp: datetime, windows: Mapping[str, tuple[int, int]]) -> str:
    minute = stamp.hour * 60 + stamp.minute
    for name, (start, end) in windows.items():
        if start <= minute < end:
            return name
    return "closed"


def _session_label(
    stamp: datetime,
    *,
    schedule: MarketHours | None,
    windows: Mapping[str, tuple[int, int]] | None,
) -> str:
    if schedule is not None:
        if not schedule.is_open(stamp):
            return "break" if schedule.is_break(stamp) else "closed"
        if windows is None:
            return "open"
        return _named_session(stamp, windows or {})
    if windows is not None:
        return _named_session(stamp, windows)
    return _default_session(stamp)


def _returns(values: Mapping[datetime, float]) -> dict[datetime, float]:
    points = sorted(values.items())
    result: dict[datetime, float] = {}
    for (previous_stamp, previous), (stamp, current) in zip(points, points[1:], strict=False):
        if stamp - previous_stamp != timedelta(minutes=1) or previous <= 0 or current <= 0:
            continue
        result[stamp] = math.log(current / previous)
    return result


def _add_session(
    sessions: dict[str, dict[str, _Reservoir]],
    session: str,
    metric: str,
    value: float,
    *,
    reservoir_size: int,
) -> None:
    metrics = sessions.setdefault(session, {})
    metrics.setdefault(metric, _Reservoir(reservoir_size)).add(value)


def calibrate(
    instrument: str,
    hist_bars: Iterable[Mapping[str, Any]],
    capital_bars: Iterable[Mapping[str, Any]],
    hist_ticks: Iterable[Mapping[str, Any]],
    *,
    session_for: Callable[[datetime], str] | None = None,
    market_hours: MarketHours | None = None,
    session_windows: Mapping[str, tuple[int, int]] | None = None,
    expected_lag_hours: int = 0,
    reservoir_size: int = DEFAULT_RESERVOIR_SIZE,
) -> CalibrationResult:
    """Compare bid bars and calculate bounded-memory spread distributions."""
    if session_for is not None and (market_hours is not None or session_windows is not None):
        raise ValueError("provide session_for or market_hours/session_windows, not both")
    if session_for is None:
        def session_label(stamp: datetime) -> str:
            return _session_label(stamp, schedule=market_hours, windows=session_windows)

        session_for = session_label

    hist: dict[datetime, float] = {}
    for row in hist_bars:
        value = _value(row, "bid", "close")
        if value is not None:
            hist[_timestamp(row)] = value

    capital: dict[datetime, float] = {}
    capital_spread = _Reservoir(reservoir_size)
    sessions: dict[str, dict[str, _Reservoir]] = {}
    for row in capital_bars:
        stamp = _timestamp(row)
        if row.get("side", "bid") != "bid":
            continue
        value = _value(row, "bid", "close")
        if value is not None:
            capital[stamp] = value
        spread = _value(row, "spread_close", "spread")
        if spread is not None and spread >= 0:
            capital_spread.add(spread)
            _add_session(
                sessions,
                session_for(stamp),
                "capital_spread",
                spread,
                reservoir_size=reservoir_size,
            )

    overlap = sorted(set(hist) & set(capital))
    basis = _Reservoir(reservoir_size)
    for stamp in overlap:
        value = hist[stamp] - capital[stamp]
        basis.add(value)
        _add_session(sessions, session_for(stamp), "basis", value, reservoir_size=reservoir_size)

    hist_spread = _Reservoir(reservoir_size)
    for row in hist_ticks:
        bid = _value(row, "bid")
        ask = _value(row, "ask")
        if bid is None or ask is None or ask < bid:
            continue
        spread = ask - bid
        hist_spread.add(spread)
        _add_session(
            sessions,
            session_for(_timestamp(row)),
            "histdata_spread",
            spread,
            reservoir_size=reservoir_size,
        )

    hist_returns, capital_returns = _returns(hist), _returns(capital)
    lag_correlations: dict[int, float] = {}
    for lag in range(-6, 7):
        paired = [
            (hist_returns[stamp + timedelta(hours=lag)], value)
            for stamp, value in capital_returns.items()
            if stamp + timedelta(hours=lag) in hist_returns
        ]
        left = [item[0] for item in paired]
        right = [item[1] for item in paired]
        if len(paired) >= 2 and len(set(left)) > 1 and len(set(right)) > 1:
            lag_correlations[lag] = correlation(left, right)
    best_lag = (
        max(lag_correlations, key=lambda lag: abs(lag_correlations[lag]))
        if lag_correlations
        else None
    )
    warnings: list[str] = []
    if not overlap:
        warnings.append("no overlapping HistData and Capital.com bid bars")
    if hist_spread.count == 0:
        warnings.append(
            "HistData tick input unavailable; cost-model export will refuse without tick spreads"
        )
    if best_lag is None:
        warnings.append("insufficient consecutive overlap for timezone correlation")
    elif best_lag != expected_lag_hours:
        warnings.append(
            f"best return correlation lag {best_lag}h differs from expected {expected_lag_hours}h"
        )
    if market_hours is None and session_windows is None:
        warnings.append(
            "using fixture session fallback; supply broker MarketHours or session config "
            "for production"
        )
    session_stats = {
        session: {name: stats.distribution() for name, stats in metrics.items()}
        for session, metrics in sessions.items()
    }
    return CalibrationResult(
        instrument=instrument,
        basis=basis.distribution(),
        histdata_spread=hist_spread.distribution(),
        capital_spread=capital_spread.distribution(),
        best_lag_hours=best_lag,
        lag_correlations=lag_correlations,
        expected_lag_hours=expected_lag_hours,
        timezone_ok=best_lag == expected_lag_hours,
        session_stats=session_stats,
        warnings=tuple(warnings),
    )


def _fmt(value: float | int | bool | None) -> str:
    return "n/a" if value is None else str(value)


def write_report(results: Iterable[CalibrationResult], path: str | Path) -> None:
    """Write full per-instrument and per-session Markdown statistics."""
    rows = list(results)
    if not rows:
        raise ValueError("refusing cost-model export: no calibration results")
    lines = [
        "# Basis and spread calibration",
        "",
        "Generated by `trader.data.calibration`; all prices are bid based.",
        "",
    ]
    for result in rows:
        lines.extend(
            [
                f"## {result.instrument}",
                "",
                f"Best return lag: **{_fmt(result.best_lag_hours)}h** "
                f"(expected {result.expected_lag_hours}h; "
                f"timezone_ok={result.timezone_ok}).",
                "",
                "| Metric | n | median | p90 | p99 |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        summary = {
            "basis": result.basis,
            "histdata_spread": result.histdata_spread,
            "capital_spread": result.capital_spread,
        }
        for name, stats in summary.items():
            lines.append(
                f"| {name} | {stats.count} | {_fmt(stats.median)} | "
                f"{_fmt(stats.p90)} | {_fmt(stats.p99)} |"
            )
        for session, metrics in sorted(result.session_stats.items()):
            lines.extend(
                [
                    "",
                    f"### Session: {session}",
                    "",
                    "| Metric | n | median | p90 | p99 |",
                    "|---|---:|---:|---:|---:|",
                ]
            )
            for name in ("basis", "histdata_spread", "capital_spread"):
                stats = metrics.get(name, Distribution(0, None, None, None))
                lines.append(
                    f"| {name} | {stats.count} | {_fmt(stats.median)} | "
                    f"{_fmt(stats.p90)} | {_fmt(stats.p99)} |"
                )
        for warning in result.warnings:
            lines.extend(["", f"> **Warning ({result.instrument}):** {warning}"])
        lines.append("")
    Path(path).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write_cost_model(
    results: Iterable[CalibrationResult],
    path: str | Path,
    lineage: Mapping[str, Any],
    *,
    min_spread_samples: int = 1000,
    min_basis_samples: int = 500,
) -> None:
    """Export PaperBroker-style costs only after a per-session sample guard."""
    rows = list(results)
    if min_spread_samples < 1 or min_basis_samples < 1:
        raise ValueError("minimum sample counts must be positive")
    missing: list[str] = []
    tradable_sessions = 0
    empty = Distribution(0, None, None, None)
    for result in rows:
        for session, metrics in result.session_stats.items():
            if session in {"closed", "break"}:
                continue
            tradable_sessions += 1
            basis_count = metrics.get("basis", empty).count
            hist_count = metrics.get("histdata_spread", empty).count
            capital_count = metrics.get("capital_spread", empty).count
            if basis_count < min_basis_samples:
                missing.append(
                    f"{result.instrument}/{session} basis {basis_count}<{min_basis_samples}"
                )
            if hist_count < min_spread_samples:
                missing.append(
                    f"{result.instrument}/{session} HistData spread "
                    f"{hist_count}<{min_spread_samples}"
                )
            if capital_count < min_spread_samples:
                missing.append(
                    f"{result.instrument}/{session} Capital.com spread "
                    f"{capital_count}<{min_spread_samples}"
                )
    if missing:
        raise ValueError(
            "refusing cost-model export: insufficient samples (" + "; ".join(missing) + ")"
        )
    if tradable_sessions == 0:
        raise ValueError("refusing cost-model export: no tradable-session samples")
    lineage_value = dict(lineage)
    lineage_value.setdefault("generated_at", datetime.now(UTC).isoformat())
    lineage_value.setdefault("git_commit", _git_commit())
    instruments: dict[str, Any] = {}
    for result in rows:
        sessions: dict[str, Any] = {}
        for session, metrics in sorted(result.session_stats.items()):
            if session in {"closed", "break"}:
                continue
            sessions[session] = {
                name: asdict(metrics[name])
                for name in ("basis", "histdata_spread", "capital_spread")
                if name in metrics
            }
        instruments[result.instrument] = {"sessions": sessions}
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump({"instruments": instruments, "lineage": lineage_value}, sort_keys=False),
        encoding="utf-8",
    )


def _iter_parquet(paths: Sequence[Path]) -> Iterable[Mapping[str, Any]]:
    if ds is None:
        raise RuntimeError("pyarrow is required for the calibration runner")
    for path in paths:
        dataset = ds.dataset(path, format="parquet")
        for batch in dataset.to_batches(batch_size=8192):
            yield from batch.to_pylist()


def _matching_rows(
    rows: Iterable[Mapping[str, Any]], instrument: str
) -> Iterable[Mapping[str, Any]]:
    for row in rows:
        labels = [row.get(name) for name in ("instrument", "symbol", "epic")]
        if any(label is not None for label in labels) and instrument not in labels:
            continue
        yield row


def _file_lineage(paths: Sequence[Path]) -> dict[str, Any]:
    inputs: list[dict[str, Any]] = []
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        row_count = (
            ds.dataset(path, format="parquet").count_rows()
            if ds is not None and path.suffix.lower() == ".parquet"
            else None
        )
        inputs.append({"path": str(path), "sha256": digest, "row_count": row_count})
    return {"inputs": inputs}


def _iter_histdata_ticks(
    paths: Sequence[Path], instrument: str
) -> Iterable[Mapping[str, Any]]:
    for path in paths:
        if not path.name.startswith(f"DAT_ASCII_{instrument}_T_"):
            continue
        yield from _matching_rows(iter_histdata_tick_rows(path, instrument), instrument)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--histdata", type=Path, required=True)
    parser.add_argument("--recorder", type=Path, required=True)
    parser.add_argument("--histdata-ticks", type=Path)
    parser.add_argument("--histdata-tick-archives", type=Path)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--cost-model", type=Path, required=True)
    parser.add_argument("--market-hours", type=Path)
    parser.add_argument("--sessions", type=Path)
    parser.add_argument("--min-spread-samples", type=int, default=1000)
    parser.add_argument("--min-basis-samples", type=int, default=500)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    hist_files = sorted(args.histdata.rglob("*.parquet"))
    capital_files = sorted(args.recorder.rglob("*.parquet"))
    tick_files = (
        sorted(args.histdata_ticks.rglob("*.parquet")) if args.histdata_ticks is not None else []
    )
    tick_archives = []
    if args.histdata_tick_archives is not None:
        tick_archives = (
            [args.histdata_tick_archives]
            if args.histdata_tick_archives.is_file()
            else sorted(args.histdata_tick_archives.rglob("*.zip"))
        )
    if not hist_files or not capital_files:
        raise SystemExit("both --histdata and --recorder must contain Parquet files")
    schedule = None
    if args.market_hours is not None:
        schedule = MarketHours.from_market_details(
            json.loads(args.market_hours.read_text(encoding="utf-8"))
        )
    session_windows = load_session_windows(args.sessions)
    tick_rows = itertools.chain(
        _matching_rows(_iter_parquet(tick_files), args.instrument),
        _iter_histdata_ticks(tick_archives, args.instrument),
    )
    result = calibrate(
        args.instrument,
        _matching_rows(_iter_parquet(hist_files), args.instrument),
        _matching_rows(_iter_parquet(capital_files), args.instrument),
        tick_rows,
        market_hours=schedule,
        session_windows=session_windows,
    )
    write_report([result], args.report)
    write_cost_model(
        [result],
        args.cost_model,
        _file_lineage([*hist_files, *capital_files, *tick_files]),
        min_spread_samples=args.min_spread_samples,
        min_basis_samples=args.min_basis_samples,
    )
    print(f"report: {args.report}")
    print(f"cost model: {args.cost_model}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
