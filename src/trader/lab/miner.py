"""Deterministic candidate pattern mining for the Strategy Lab.

The miner deliberately emits ordinary :class:`StrategySpec` values.  It does
not execute generated code, read the holdout, call an LLM, or talk to a broker.
Every candidate is recorded in the experiment ledger before an optional
validator is called, so failed candidates still count towards multiple-testing
correction.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from trader.data.resample import Candle
from trader.lab.engine import evaluate_entries
from trader.lab.ledger import ExperimentLedger, ExperimentLedgerProtocol, ExperimentRecord
from trader.lab.spec import (
    ComplexityLimits,
    IndicatorCondition,
    StopExit,
    StrategySpec,
)
from trader.lab.validator import ValidationResult, strategy_hash

PatternName = Literal[
    "opening_range",
    "session_effect",
    "volatility_compression",
    "mean_reversion",
    "event_reaction",
]
PATTERN_NAMES: tuple[PatternName, ...] = (
    "opening_range",
    "session_effect",
    "volatility_compression",
    "mean_reversion",
    "event_reaction",
)


@dataclass(frozen=True, slots=True)
class PatternCandidate:
    """One declarative strategy hypothesis emitted by :class:`PatternMiner`."""

    name: PatternName
    strategy: StrategySpec
    hypotheses_tested: int
    signals: int
    strategy_hash: str
    validation: ValidationResult | None = None

    @property
    def promoted(self) -> bool:
        """Whether the supplied validator accepted this candidate."""
        return self.validation is not None and self.validation.passed

    @property
    def spec(self) -> StrategySpec:
        """Compatibility alias used by callers that call these specs directly."""
        return self.strategy

    @property
    def hypotheses(self) -> int:
        return self.hypotheses_tested


@dataclass(frozen=True, slots=True)
class MiningResult:
    """Immutable result of one scan, including all tested hypotheses."""

    candidates: tuple[PatternCandidate, ...]
    hypotheses_tested: int
    promoted: tuple[PatternCandidate, ...]

    @property
    def strategies(self) -> tuple[StrategySpec, ...]:
        return tuple(item.strategy for item in self.candidates)

    @property
    def total_hypotheses(self) -> int:
        return self.hypotheses_tested

    @property
    def promoted_strategies(self) -> tuple[StrategySpec, ...]:
        return tuple(item.strategy for item in self.promoted)


Validator = Callable[..., ValidationResult | bool | None]


def _instrument(candles: Sequence[Candle], explicit: str | None) -> str:
    if explicit:
        return explicit
    if candles and candles[0].series_key:
        value = candles[0].series_key[0].upper()
        if value and all(ch.isalnum() or ch == "_" for ch in value):
            return value[:32]
    raise ValueError("instrument is required when candles have no series key")


def _validate_candles(candles: Sequence[Candle]) -> None:
    if not candles:
        return
    keys = {item.series_key for item in candles}
    if len(keys) > 1:
        raise ValueError("pattern mining requires one candle series")
    for item in candles:
        values = (item.open, item.high, item.low, item.close, item.volume)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("candle values must be finite")


def _condition(pattern: PatternName, direction: str) -> IndicatorCondition:
    """Build one simple, bounded condition for a known pattern family."""
    if pattern == "opening_range":
        return IndicatorCondition(
            indicator="close",
            operator="gt" if direction == "long" else "lt",
            threshold=100.0,
        )
    if pattern == "session_effect":
        return IndicatorCondition(
            indicator="session_vwap",
            operator="gt" if direction == "long" else "lt",
            threshold=100.0,
        )
    if pattern == "volatility_compression":
        return IndicatorCondition(
            indicator="atr",
            period=14,
            operator="lt" if direction == "long" else "gt",
            threshold=0.5,
        )
    if pattern == "mean_reversion":
        return IndicatorCondition(
            indicator="rsi",
            period=14,
            operator="lt" if direction == "long" else "gt",
            threshold=30.0 if direction == "long" else 70.0,
        )
    return IndicatorCondition(
        indicator="ema",
        period=9,
        operator="gt" if direction == "long" else "lt",
        threshold=100.0,
    )


def _session(pattern: PatternName) -> tuple[str, ...]:
    if pattern == "opening_range":
        return ("london", "new_york")
    if pattern == "session_effect":
        return ("asia", "london", "new_york")
    return ()


class PatternMiner:
    """Scan five documented pattern families into safe strategy specs.

    ``validator`` is optional so a scan can be used offline to inspect
    hypotheses.  When supplied, it is called after the ledger append.  It may
    accept ``(metrics, holdout_spec_hash=...)``, ``(candidate, candles)`` or a
    single ``metrics`` argument; this keeps the miner independent of the gate
    implementation while allowing the real validator to be used unchanged.
    """

    def __init__(
        self,
        *,
        instrument: str | None = None,
        timeframe_minutes: Literal[1, 3, 5] = 1,
        ledger: ExperimentLedgerProtocol | None = None,
        complexity_limits: ComplexityLimits | None = None,
        proposer: str = "pattern_miner",
        validator: Validator | None = None,
    ) -> None:
        if instrument is not None and (
            not instrument
            or len(instrument) > 32
            or not all(c.isalnum() or c == "_" for c in instrument)
        ):
            raise ValueError("instrument must contain only letters, digits and underscores")
        self.instrument = instrument.upper() if instrument else None
        self.timeframe_minutes = timeframe_minutes
        # Keep a ledger even for the convenience constructor: a scan must never
        # silently omit failed hypotheses from the multiple-testing population.
        self.ledger = ledger or ExperimentLedger()
        self.complexity_limits = complexity_limits or ComplexityLimits(
            max_conditions_per_strategy=5,
            max_tunable_parameters=4,
        )
        self.proposer = proposer
        self.validator = validator

    def _spec(
        self,
        name: PatternName,
        direction: Literal["long", "short"],
        instrument: str,
    ) -> StrategySpec:
        return StrategySpec(
            schema_version=1,
            instrument=instrument,
            direction=direction,
            timeframe_minutes=self.timeframe_minutes,
            entry_conditions=(_condition(name, direction),),
            exit_rules=(StopExit(kind="stop", distance=1.5),),
            tunable_parameters=(),
            session_filters=_session(name),
        )

    @staticmethod
    def _metrics(
        name: PatternName,
        direction: str,
        signals: int,
        candles: Sequence[Candle],
    ) -> dict[str, Any]:
        return {
            "pattern": name,
            "direction": direction,
            "signals": signals,
            "bars": len(candles),
            "candidate": True,
        }

    @staticmethod
    def _call_validator(
        validator: Validator,
        candidate: PatternCandidate,
        metrics: Mapping[str, Any],
    ) -> ValidationResult | None:
        try:
            result = validator(metrics, holdout_spec_hash=candidate.strategy_hash)
        except TypeError:
            try:
                result = validator(candidate, metrics)
            except TypeError:
                result = validator(metrics)
        if isinstance(result, ValidationResult):
            return result
        if isinstance(result, bool):
            return ValidationResult(
                passed=result,
                reasons=() if result else ("validator_rejected",),
                adjusted_alpha=0.0,
                trials=candidate.hypotheses_tested,
                strategy_hash=candidate.strategy_hash,
                holdout_read=False,
                metrics=metrics,
            )
        return None

    def scan(
        self,
        candles: Iterable[Candle],
        *,
        validator: Validator | None = None,
    ) -> MiningResult:
        source = tuple(candles)
        validator = validator or self.validator
        _validate_candles(source)
        instrument = _instrument(source, self.instrument)
        result: list[PatternCandidate] = []
        total_hypotheses = len(PATTERN_NAMES) * 2
        for name in PATTERN_NAMES:
            for direction in ("long", "short"):
                spec = self._spec(name, direction, instrument)
                spec_hash = strategy_hash(spec.model_dump(mode="json"))
                signals = len(evaluate_entries(spec, source)) if source else 0
                metrics = self._metrics(name, direction, signals, source)
                if self.ledger is not None:
                    start = source[0].ts_utc if source else datetime.now(UTC)
                    end = source[-1].ts_utc if source else start
                    self.ledger.append(
                        ExperimentRecord(
                            strategy_hash=spec_hash,
                            parameters={"pattern": name, "direction": direction},
                            data_start=start,
                            data_end=end,
                            metrics=metrics,
                            proposer=self.proposer,
                        )
                    )
                candidate = PatternCandidate(name, spec, total_hypotheses, signals, spec_hash)
                validation = (
                    self._call_validator(validator, candidate, metrics) if validator else None
                )
                result.append(
                    PatternCandidate(name, spec, total_hypotheses, signals, spec_hash, validation)
                )
        promoted = tuple(item for item in result if item.promoted)
        return MiningResult(tuple(result), total_hypotheses, promoted)

    mine = scan


def mine_patterns(
    candles: Iterable[Candle],
    *,
    instrument: str | None = None,
    ledger: ExperimentLedgerProtocol | None = None,
    validator: Validator | None = None,
) -> MiningResult:
    """Convenience wrapper for one deterministic scan."""
    return PatternMiner(instrument=instrument, ledger=ledger).scan(candles, validator=validator)


__all__ = [
    "PATTERN_NAMES",
    "MiningResult",
    "PatternCandidate",
    "PatternMiner",
    "mine_patterns",
]
