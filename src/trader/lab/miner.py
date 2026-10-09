"""Deterministic candidate pattern mining for the Strategy Lab.

Candidates are ordinary :class:`StrategySpec` values. The miner never
executes generated code, opens the sealed holdout, calls an LLM, or talks to a
broker. Every backtested variant is recorded before optional validation.
"""

from __future__ import annotations

import logging
import math
import multiprocessing
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from trader.data.resample import Candle
from trader.lab.backtest import BacktestResult, CostModel, run_backtest
from trader.lab.engine import IndicatorCache, evaluate_entries
from trader.lab.ledger import ExperimentLedger, ExperimentLedgerProtocol, ExperimentRecord
from trader.lab.metrics import gate1_metrics
from trader.lab.spec import ComplexityLimits, IndicatorCondition, StopExit, StrategySpec, TargetExit
from trader.lab.validator import ValidationResult, strategy_hash

SessionName = Literal["asia", "london", "new_york", "closed"]
PatternName = Literal[
    "opening_range",
    "session_effect",
    "volatility_compression",
    "volatility_expansion",
    "mean_reversion",
]
PATTERN_NAMES: tuple[PatternName, ...] = (
    "opening_range",
    "session_effect",
    "volatility_compression",
    "volatility_expansion",
    "mean_reversion",
)
DEFERRED_PATTERN_NAMES = ("event_reaction",)
LOGGER = logging.getLogger(__name__)

_MINING_CONTEXT: (
    tuple[
        tuple[Candle, ...],
        CostModel | None,
        float,
        int | None,
        int | None,
        int | None,
    ]
    | None
) = None


def _init_mining_worker(
    candles: tuple[Candle, ...],
    cost_model: CostModel | None,
    cost_stress_pct: float,
    train_bars: int | None,
    test_bars: int | None,
    step_bars: int | None,
) -> None:
    """Install an immutable mining context in each forked worker."""
    global _MINING_CONTEXT
    _MINING_CONTEXT = (
        tuple(candles),
        cost_model,
        cost_stress_pct,
        train_bars,
        test_bars,
        step_bars,
    )
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except (OSError, PermissionError):
            pass


def _mine_one(
    spec: StrategySpec,
    candles: Sequence[Candle],
    cost_model: CostModel | None,
    cost_stress_pct: float,
    train_bars: int | None,
    test_bars: int | None,
    step_bars: int | None,
) -> tuple[BacktestResult, BacktestResult, int]:
    """Run both deterministic cost variants while sharing indicators/signals."""
    cache = IndicatorCache.from_candles(candles, spec.timeframe_minutes)
    signals = evaluate_entries(spec, cache.bars, indicator_cache=cache) if cache.bars else ()
    result = run_backtest(
        spec,
        candles,
        cost_model=cost_model,
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
        indicator_cache=cache,
        entry_signals=signals,
    )
    stress = run_backtest(
        spec,
        candles,
        cost_model=cost_model,
        cost_stress_pct=cost_stress_pct,
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
        indicator_cache=cache,
        entry_signals=signals,
    )
    return result, stress, len(signals)


def _mine_one_worker(
    item: tuple[int, StrategySpec],
) -> tuple[int, BacktestResult, BacktestResult, int]:
    if _MINING_CONTEXT is None:
        raise RuntimeError("mining worker context is not initialized")
    candles, cost_model, cost_stress_pct, train_bars, test_bars, step_bars = _MINING_CONTEXT
    result, stress, signals = _mine_one(
        item[1], candles, cost_model, cost_stress_pct, train_bars, test_bars, step_bars
    )
    return item[0], result, stress, signals


class GateValidator(Protocol):
    def __call__(
        self, metrics: Mapping[str, Any], *, holdout_spec_hash: str
    ) -> ValidationResult: ...


@dataclass(frozen=True, slots=True)
class PatternCandidate:
    """One tested strategy hypothesis emitted by :class:`PatternMiner`."""

    name: PatternName
    strategy: StrategySpec
    hypotheses_tested: int
    signals: int
    strategy_hash: str
    validation: ValidationResult | None = None
    backtest: BacktestResult | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def promoted(self) -> bool:
        return self.validation is not None and self.validation.passed

    @property
    def spec(self) -> StrategySpec:
        return self.strategy

    @property
    def hypotheses(self) -> int:
        return self.hypotheses_tested


@dataclass(frozen=True, slots=True)
class MiningResult:
    candidates: tuple[PatternCandidate, ...]
    hypotheses_tested: int
    promoted: tuple[PatternCandidate, ...]

    @property
    def strategies(self) -> tuple[StrategySpec, ...]:
        return tuple(item.strategy for item in self.candidates)

    @property
    def promoted_strategies(self) -> tuple[StrategySpec, ...]:
        return tuple(item.strategy for item in self.promoted)

    @property
    def total_hypotheses(self) -> int:
        return self.hypotheses_tested


def _instrument(candles: Sequence[Candle], explicit: str | None) -> str:
    if explicit:
        return explicit
    if candles and candles[0].series_key:
        value = candles[0].series_key[0].upper()
        if value and all(ch.isalnum() or ch == "_" for ch in value):
            return value[:32]
    raise ValueError("instrument is required when candles have no series key")


def _validate_candles(candles: Sequence[Candle]) -> None:
    if len({item.series_key for item in candles}) > 1:
        raise ValueError("pattern mining requires one candle series")
    for item in candles:
        values = (item.open, item.high, item.low, item.close, item.volume)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("candle values must be finite")


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.5
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _thresholds(name: PatternName, candles: Sequence[Candle]) -> tuple[float, ...]:
    closes = [float(item.close) for item in candles]
    ranges = [float(item.high - item.low) for item in candles]
    baseline = closes[0] if closes else 100.0
    if name == "opening_range":
        opening = closes[: min(30, len(closes))]
        baseline = max(opening) if opening else baseline
        return (baseline, baseline + max(_median(ranges), 0.01))
    if name == "session_effect":
        return (baseline, _median(closes))
    if name == "volatility_compression":
        return (max(_median(ranges), 0.01), max(_median(ranges) * 0.75, 0.01))
    if name == "volatility_expansion":
        return (max(_median(ranges) * 1.25, 0.01), max(_median(ranges) * 1.5, 0.01))
    return (30.0, 35.0, 65.0, 70.0)


def _condition(
    pattern: PatternName,
    direction: Literal["long", "short"],
    threshold: float,
) -> IndicatorCondition:
    if pattern in {"opening_range", "session_effect"}:
        return IndicatorCondition(
            indicator="close" if pattern == "opening_range" else "session_vwap",
            operator="gt" if direction == "long" else "lt",
            threshold=float(threshold),
        )
    if pattern in {"volatility_compression", "volatility_expansion"}:
        return IndicatorCondition(
            indicator="atr",
            period=14,
            operator=("lt" if pattern == "volatility_compression" else "gt"),
            threshold=float(threshold),
        )
    return IndicatorCondition(
        indicator="rsi",
        period=14,
        operator="lt" if direction == "long" else "gt",
        threshold=float(threshold),
    )


def _sessions(pattern: PatternName) -> tuple[SessionName, ...]:
    if pattern == "opening_range":
        return ("london", "new_york")
    if pattern == "session_effect":
        return ("asia", "london", "new_york")
    return ()


class PatternMiner:
    """Mine bounded pattern grids and send derived metrics to Gate 1."""

    def __init__(
        self,
        *,
        instrument: str | None = None,
        timeframe_minutes: Literal[1, 3, 5] = 1,
        ledger: ExperimentLedgerProtocol | None = None,
        complexity_limits: ComplexityLimits | None = None,
        proposer: str = "pattern_miner",
        validator: GateValidator | None = None,
        cost_model: CostModel | None = None,
        cost_stress_pct: float = 50.0,
        train_bars: int | None = None,
        test_bars: int | None = None,
        step_bars: int | None = None,
        workers: int | None = 1,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        if instrument is not None and (
            not instrument
            or len(instrument) > 32
            or not all(c.isalnum() or c == "_" for c in instrument)
        ):
            raise ValueError("instrument must contain only letters, digits and underscores")
        if not math.isfinite(cost_stress_pct) or cost_stress_pct < 0:
            raise ValueError("cost_stress_pct must be finite and non-negative")
        if workers is not None and workers < 1:
            raise ValueError("workers must be positive")
        self.instrument = instrument.upper() if instrument else None
        self.timeframe_minutes = timeframe_minutes
        self.ledger = ledger or ExperimentLedger()
        self.complexity_limits = complexity_limits or ComplexityLimits(
            max_conditions_per_strategy=5,
            max_tunable_parameters=4,
        )
        self.proposer = proposer
        self.validator = validator
        self.cost_model = cost_model
        self.cost_stress_pct = cost_stress_pct
        self.train_bars = train_bars
        self.test_bars = test_bars
        self.step_bars = step_bars
        self.workers = workers
        self.progress_callback = progress_callback

    def _spec(
        self,
        name: PatternName,
        direction: Literal["long", "short"],
        threshold: float,
        instrument: str,
    ) -> StrategySpec:
        return StrategySpec(
            schema_version=1,
            instrument=instrument,
            direction=direction,
            timeframe_minutes=self.timeframe_minutes,
            entry_conditions=(_condition(name, direction, threshold),),
            exit_rules=(
                StopExit(kind="stop", distance=1.5),
                TargetExit(kind="target", distance=1.0),
            ),
            session_filters=_sessions(name),
        )

    def scan(
        self,
        candles: Iterable[Candle],
        *,
        validator: GateValidator | None = None,
        neighbor_results: Sequence[BacktestResult] = (),
        workers: int | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> MiningResult:
        source = tuple(candles)
        _validate_candles(source)
        instrument = _instrument(source, self.instrument)
        gate_validator = validator or self.validator
        callback = progress_callback or self.progress_callback
        specs: list[tuple[PatternName, StrategySpec, float]] = []
        for name in PATTERN_NAMES:
            for direction in ("long", "short"):
                for threshold in _thresholds(name, source):
                    if name == "mean_reversion" and (
                        (direction == "long" and threshold > 50)
                        or (direction == "short" and threshold < 50)
                    ):
                        continue
                    spec = self._spec(name, direction, threshold, instrument)
                    specs.append((name, spec, threshold))

        worker_count = self.workers if workers is None else workers
        if worker_count is None:
            # Keep the miner's standalone API aligned with the runner's
            # cgroup-aware worker cap without importing runner at module load.
            from trader.lab.runner import cap_worker_count, read_memory_limit

            worker_count = cap_worker_count(
                cpu_count=os.cpu_count(),
                memory_limit_bytes=read_memory_limit(),
            )
        work: dict[int, tuple[BacktestResult, BacktestResult, int]] = {}

        def report(done: int) -> None:
            if callback is not None:
                callback(done, len(specs))

        if worker_count > 1 and len(specs) > 1:
            try:
                with ProcessPoolExecutor(
                    max_workers=worker_count,
                    mp_context=multiprocessing.get_context("fork" if os.name != "nt" else "spawn"),
                    initializer=_init_mining_worker,
                    initargs=(
                        source,
                        self.cost_model,
                        self.cost_stress_pct,
                        self.train_bars,
                        self.test_bars,
                        self.step_bars,
                    ),
                ) as pool:
                    futures = {
                        pool.submit(_mine_one_worker, (index, spec)): index
                        for index, (_, spec, _) in enumerate(specs)
                    }
                    completed = 0
                    for future in as_completed(futures):
                        index, result, stress, signals = future.result()
                        work[index] = (result, stress, signals)
                        completed += 1
                        report(completed)
            except Exception as exc:  # noqa: BLE001 - serial fallback keeps results identical
                # A worker failure must not change candidate ordering or make
                # the offline miner unusable on platforms without fork. Say so
                # loudly: the serial path is many times slower.
                LOGGER.warning(
                    "parallel mining failed (%s); falling back to serial for %d specs",
                    type(exc).__name__,
                    len(specs),
                )
                work.clear()

        if len(work) != len(specs):
            work.clear()
            for index, (_, spec, _) in enumerate(specs):
                work[index] = _mine_one(
                    spec,
                    source,
                    self.cost_model,
                    self.cost_stress_pct,
                    self.train_bars,
                    self.test_bars,
                    self.step_bars,
                )
                report(index + 1)

        candidates: list[PatternCandidate] = []
        for index, (name, spec, threshold) in enumerate(specs):
            result, stress, signal_count = work[index]
            metrics = dict(
                gate1_metrics(
                    result,
                    cost_stress=stress,
                    neighbor_results=neighbor_results,
                )
            )
            metrics.update({"pattern": name, "direction": spec.direction, "threshold": threshold})
            spec_hash = strategy_hash(spec.model_dump(mode="json"))
            self.ledger.append(
                ExperimentRecord(
                    strategy_hash=spec_hash,
                    parameters={
                        "pattern": name,
                        "direction": spec.direction,
                        "threshold": threshold,
                    },
                    data_start=source[0].ts_utc if source else datetime.now(UTC),
                    data_end=source[-1].ts_utc if source else datetime.now(UTC),
                    metrics=metrics,
                    proposer=self.proposer,
                )
            )
            validation = (
                gate_validator(metrics, holdout_spec_hash=spec_hash)
                if gate_validator is not None
                else None
            )
            candidates.append(
                PatternCandidate(
                    name=name,
                    strategy=spec,
                    hypotheses_tested=self.ledger.count(),
                    signals=signal_count,
                    strategy_hash=spec_hash,
                    validation=validation,
                    backtest=result,
                    metrics=metrics,
                )
            )
        total = self.ledger.count()
        normalized = tuple(
            PatternCandidate(
                name=item.name,
                strategy=item.strategy,
                hypotheses_tested=total,
                signals=item.signals,
                strategy_hash=item.strategy_hash,
                validation=item.validation,
                backtest=item.backtest,
                metrics=item.metrics,
            )
            for item in candidates
        )
        return MiningResult(normalized, total, tuple(item for item in normalized if item.promoted))

    mine = scan


def mine_patterns(
    candles: Iterable[Candle],
    *,
    instrument: str | None = None,
    ledger: ExperimentLedgerProtocol | None = None,
    validator: GateValidator | None = None,
    workers: int | None = 1,
    progress_callback: Callable[[int, int], None] | None = None,
) -> MiningResult:
    return PatternMiner(
        instrument=instrument,
        ledger=ledger,
        validator=validator,
        workers=workers,
        progress_callback=progress_callback,
    ).scan(candles)


__all__ = [
    "DEFERRED_PATTERN_NAMES",
    "GateValidator",
    "MiningResult",
    "PATTERN_NAMES",
    "PatternCandidate",
    "PatternMiner",
    "mine_patterns",
]
