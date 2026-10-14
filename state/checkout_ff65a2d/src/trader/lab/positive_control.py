"""Synthetic markets with a planted, known edge for the Strategy Lab positive control.

Nothing here reads real data, a broker or the sealed holdout.  The generator makes a
driftless random walk with volatility clustering.  A *planted* edge is added by rule R:
after a 5-minute bar whose close is at least ``PLANT_DROP_ATR`` x ATR(14) below its open
(the same condition the declarative control strategy tests), the next
``PLANT_BARS`` five-minute bars receive an extra per-minute drift.  The drift size is
calibrated with the real backtest engine so that the control strategy's average gross
result per trade equals a chosen multiple of the round-trip cost.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from trader.data.resample import Candle
from trader.lab.spec import (
    IndicatorCondition,
    ParameterReference,
    StopExit,
    StrategySpec,
    TargetExit,
    TimeStopExit,
    TunableParameter,
)

BLOCK_MINUTES = 5
ATR_PERIOD = 14
PLANT_DROP_ATR = 1.0  # loosest drop that carries the edge; tuned neighbours are subsets
PLANT_BARS = 12
CONTROL_STOP_ATR = 1.5
CONTROL_TARGET_ATR = 3.0
CONTROL_TIME_STOP_BARS = 36
ROUND_TRIP_COST_POINTS = 3.0  # NSXUSD spread 2.0 + slippage 1.0, unchanged
DEFAULT_START = datetime(2021, 1, 4, tzinfo=UTC)  # a Monday
START_LEVEL = 15_000.0
MINUTE_SIGMA = 0.9  # points; one-minute ATR is about 1.5 points
SIGMA_PERSISTENCE = 0.98  # per five-minute block, in log volatility
SIGMA_NOISE = 0.06


def planted_strategy(instrument: str = "NSXUSD", timeframe: int = 5) -> StrategySpec:
    """Return the declarative rule that sees the planted edge at entry.

    Long when the bar closes more than ``drop_atr`` x ATR below its open; ATR stop 1.5,
    ATR target 3.0, 36-bar time stop.  The tunable parameter gives Gate 1's neighbour test
    something to move; every neighbour value is looser than or equal to the planted rule's
    own threshold, so each neighbour's trades contain the planted ones.
    """
    return StrategySpec(
        schema_version=1,
        instrument=instrument,
        direction="long",
        timeframe_minutes=timeframe,  # type: ignore[arg-type]
        entry_conditions=(
            IndicatorCondition(
                indicator="close",
                operator="lt",
                threshold=0.0,
                threshold_indicator="open",
                threshold_atr_multiple=ParameterReference(parameter="drop_atr"),
            ),
        ),
        exit_rules=(
            StopExit(kind="stop", distance=CONTROL_STOP_ATR, unit="atr"),
            TargetExit(kind="target", distance=CONTROL_TARGET_ATR, unit="atr"),
            TimeStopExit(kind="time_stop", bars=CONTROL_TIME_STOP_BARS),
        ),
        tunable_parameters=(
            TunableParameter(
                name="drop_atr",
                value=-PLANT_DROP_ATR * 1.25,
                minimum=-PLANT_DROP_ATR * 1.5,
                maximum=-PLANT_DROP_ATR,
            ),
        ),
    )


def minute_timestamps(days: int, start: datetime = DEFAULT_START) -> list[datetime]:
    """Weekday-only one-minute timestamps for ``days`` trading days from ``start``."""
    if days < 1:
        raise ValueError("days must be positive")
    if start.weekday() != 0 or start.tzinfo is None:
        raise ValueError("start must be a timezone-aware Monday midnight")
    stamps: list[datetime] = []
    day = start
    produced = 0
    while produced < days:
        if day.weekday() < 5:
            stamps.extend(day + timedelta(minutes=minute) for minute in range(1440))
            produced += 1
        day += timedelta(days=1)
    return stamps


def generate_minutes(
    seed: int,
    days: int,
    *,
    drift: float = 0.0,
    plant: bool = True,
    start: datetime = DEFAULT_START,
) -> list[Candle]:
    """Generate one-minute candles.  ``drift`` is extra points per minute after rule R."""
    stamps = minute_timestamps(days, start)
    rng = random.Random(seed)
    candles: list[Candle] = []
    price = START_LEVEL
    log_sigma = 0.0
    atr: float | None = None
    true_ranges: list[float] = []
    previous_close: float | None = None
    remaining = 0
    for block_start in range(0, len(stamps), BLOCK_MINUTES):
        log_sigma = SIGMA_PERSISTENCE * log_sigma + SIGMA_NOISE * rng.gauss(0.0, 1.0)
        sigma = MINUTE_SIGMA * math.exp(log_sigma)
        extra = drift if remaining > 0 else 0.0
        block_open = price
        block_high = -math.inf
        block_low = math.inf
        for offset in range(BLOCK_MINUTES):
            step = sigma * rng.gauss(0.0, 1.0) + extra
            opened = price
            closed = max(1.0, opened + step)
            high = max(opened, closed) + abs(rng.gauss(0.0, 0.35)) * sigma
            low = min(opened, closed) - abs(rng.gauss(0.0, 0.35)) * sigma
            candles.append(
                Candle(
                    ts_utc=stamps[block_start + offset],
                    open=opened,
                    high=high,
                    low=low,
                    close=closed,
                    volume=100.0,
                )
            )
            block_high = max(block_high, high)
            block_low = min(block_low, low)
            price = closed
        true_range = block_high - block_low
        if previous_close is not None:
            true_range = max(
                true_range, abs(block_high - previous_close), abs(block_low - previous_close)
            )
        previous_close = price
        if atr is None:
            true_ranges.append(true_range)
            if len(true_ranges) == ATR_PERIOD:
                atr = math.fsum(true_ranges) / ATR_PERIOD
        else:
            atr = (atr * (ATR_PERIOD - 1) + true_range) / ATR_PERIOD
        remaining = max(0, remaining - 1)
        if plant and atr is not None and price - block_open <= -PLANT_DROP_ATR * atr:
            remaining = PLANT_BARS
    return candles


def gross_per_trade(candles: list[Candle], instrument: str = "NSXUSD") -> tuple[float, int]:
    """Average gross points per trade of the control strategy at zero cost, and trade count."""
    from trader.lab.backtest import run_backtest

    result = run_backtest(planted_strategy(instrument), candles)
    count = len(result.trades)
    return (result.gross_pnl / count if count else 0.0), count


def calibrate_drift(
    target_gross: float,
    *,
    seed: int,
    days: int,
    low: float = -0.4,
    high: float = 0.8,
    iterations: int = 12,
    measure: Callable[[float], tuple[float, int]] | None = None,
) -> dict[str, Any]:
    """Bisect the per-minute drift so the control strategy's gross per trade hits the target."""

    def default_measure(drift: float) -> tuple[float, int]:
        return gross_per_trade(generate_minutes(seed, days, drift=drift, plant=True))

    probe = measure or default_measure
    lo, hi = low, high
    best = (0.0, 0.0, 0)
    for _ in range(iterations):
        mid = (lo + hi) / 2
        value, trades = probe(mid)
        best = (mid, value, trades)
        if value < target_gross:
            lo = mid
        else:
            hi = mid
    return {
        "drift_per_minute": best[0],
        "measured_gross_per_trade": best[1],
        "trades": best[2],
        "target_gross_per_trade": target_gross,
        "seed": seed,
        "calibration_days": days,
    }
