"""Deterministic basis, spread and timezone calibration for T-017."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import correlation, median
from typing import Any


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


def _distribution(values: Iterable[float]) -> Distribution:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return Distribution(0, None, None, None)

    def percentile(rank: float) -> float:
        index = (len(ordered) - 1) * rank
        lower, upper = math.floor(index), math.ceil(index)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)

    return Distribution(len(ordered), median(ordered), percentile(0.90), percentile(0.99))


def _default_session(stamp: datetime) -> str:
    hour = stamp.hour
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 13:
        return "london"
    if 13 <= hour < 22:
        return "new_york"
    return "closed"


def _returns(values: Mapping[datetime, float]) -> dict[datetime, float]:
    points = sorted(values.items())
    result: dict[datetime, float] = {}
    for (previous_stamp, previous), (stamp, current) in zip(points, points[1:], strict=False):
        if stamp - previous_stamp != timedelta(minutes=1) or previous <= 0 or current <= 0:
            continue
        result[stamp] = math.log(current / previous)
    return result


def calibrate(
    instrument: str,
    hist_bars: Iterable[Mapping[str, Any]],
    capital_bars: Iterable[Mapping[str, Any]],
    hist_ticks: Iterable[Mapping[str, Any]],
    *,
    session_for: Callable[[datetime], str] = _default_session,
    expected_lag_hours: int = 0,
) -> CalibrationResult:
    """Compare bid bars and calculate spread distributions without guessing costs."""
    hist: dict[datetime, float] = {}
    for row in hist_bars:
        value = _value(row, "bid", "close")
        if value is not None:
            hist[_timestamp(row)] = value
    capital: dict[datetime, float] = {}
    for row in capital_bars:
        if row.get("side", "bid") != "bid":
            continue
        value = _value(row, "bid", "close")
        if value is not None:
            capital[_timestamp(row)] = value
    overlap = sorted(set(hist) & set(capital))
    basis_values = [hist[stamp] - capital[stamp] for stamp in overlap]
    hist_spreads = [
        ask - bid
        for row in hist_ticks
        if (bid := _value(row, "bid")) is not None and (ask := _value(row, "ask")) is not None
        for _ in [0]
        if ask >= bid
    ]
    capital_spreads = [
        spread
        for row in capital_bars
        if (spread := _value(row, "spread_close", "spread")) is not None and spread >= 0
    ]
    hist_returns, capital_returns = _returns(hist), _returns(capital)
    lag_correlations: dict[int, float] = {}
    for lag in range(-6, 7):
        shifted = {
            stamp: value
            for stamp, value in capital_returns.items()
            if stamp + timedelta(hours=lag) in hist_returns
        }
        paired = [
            (hist_returns[stamp + timedelta(hours=lag)], value) for stamp, value in shifted.items()
        ]
        if (
            len(paired) >= 2
            and len({left for left, _ in paired}) > 1
            and len({right for _, right in paired}) > 1
        ):
            left = [item[0] for item in paired]
            right = [item[1] for item in paired]
            lag_correlations[lag] = correlation(left, right)
    best_lag = (
        max(lag_correlations, key=lambda lag: abs(lag_correlations[lag]))
        if lag_correlations
        else None
    )
    sessions: dict[str, dict[str, list[float]]] = {}
    for stamp in overlap:
        sessions.setdefault(session_for(stamp), {}).setdefault("basis", []).append(
            hist[stamp] - capital[stamp]
        )
    for row in capital_bars:
        spread = _value(row, "spread_close", "spread")
        if spread is not None and spread >= 0:
            sessions.setdefault(session_for(_timestamp(row)), {}).setdefault(
                "capital_spread", []
            ).append(spread)
    session_stats = {
        session: {name: _distribution(values) for name, values in stats.items()}
        for session, stats in sessions.items()
    }
    warnings: list[str] = []
    if not overlap:
        warnings.append("no overlapping HistData and Capital.com bid bars")
    if best_lag is None:
        warnings.append("insufficient consecutive overlap for timezone correlation")
    elif best_lag != expected_lag_hours:
        warnings.append(
            f"best return correlation lag {best_lag}h differs from expected {expected_lag_hours}h"
        )
    return CalibrationResult(
        instrument=instrument,
        basis=_distribution(basis_values),
        histdata_spread=_distribution(hist_spreads),
        capital_spread=_distribution(capital_spreads),
        best_lag_hours=best_lag,
        lag_correlations=lag_correlations,
        expected_lag_hours=expected_lag_hours,
        timezone_ok=best_lag == expected_lag_hours,
        session_stats=session_stats,
        warnings=tuple(warnings),
    )


def write_report(results: Iterable[CalibrationResult], path: str | Path) -> None:
    """Write a reviewable Markdown report; absent data remains explicitly uncalibrated."""
    rows = list(results)
    lines = [
        "# Basis and spread calibration",
        "",
        "Generated by `trader.data.calibration`; all prices are bid based.",
        "",
        "| Instrument | Basis n | Basis median | Hist spread p90 | "
        "Capital spread p90 | Best lag (h) | Timezone |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for result in rows:
        basis_median = result.basis.median if result.basis.median is not None else "n/a"
        hist_p90 = result.histdata_spread.p90 if result.histdata_spread.p90 is not None else "n/a"
        capital_p90 = result.capital_spread.p90 if result.capital_spread.p90 is not None else "n/a"
        lag = result.best_lag_hours if result.best_lag_hours is not None else "n/a"
        lines.append(
            f"| {result.instrument} | {result.basis.count} | {basis_median} | "
            f"{hist_p90} | {capital_p90} | {lag} | {result.timezone_ok} |"
        )
        for warning in result.warnings:
            lines.append(f"\n> **Warning ({result.instrument}):** {warning}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
