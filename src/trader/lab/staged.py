"""Staged mutate-and-retest search (docs/specs/staged_search.md).

Parents (strategies that nearly worked) are changed one field at a time from a FIXED menu.
Children are ranked on the EARLY part of the training window and must also be profitable
after costs on the LATER training window and on the OTHER market.  Survivors become the next
generation's parents (at most three generations).  Every distinct spec evaluated anywhere in
the search is counted, including variants dropped early, and that single global count is what
the final Gate 1 run receives as ``trials``.

Nothing here reads the sealed holdout or the pipeline's out-of-sample slice, changes a gate or
a cost, or runs generated code: mutations are edits of declarative ``StrategySpec`` values.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from trader.data.resample import Candle
from trader.lab.backtest import CostModel, run_backtest
from trader.lab.engine import IndicatorCache
from trader.lab.spec import (
    IndicatorCondition,
    SessionName,
    StopExit,
    StrategySpec,
    TargetExit,
    TimeStopExit,
)
from trader.lab.validator import strategy_hash

STOP_TARGETS = ((1.0, 2.0), (1.5, 3.0), (2.0, 4.0), (2.5, 5.0), (3.0, 6.0))
TIME_STOPS = (12, 24, 36, 72)
SESSION_SETS: tuple[tuple[SessionName, ...], ...] = (
    (),
    ("london",),
    ("new_york",),
    ("london", "new_york"),
)
VOLATILITY_REGIMES = ("normal_volatility", "high_volatility")
MAX_CONDITIONS = 5
MAX_CHILDREN_PER_PARENT = 40
MAX_GENERATIONS = 3
EARLY_FRACTION = 0.6
MIN_WINDOW_TRADES = 100


@dataclass(frozen=True, slots=True)
class WindowStats:
    trades: int
    net_per_trade: float
    gross_per_trade: float


Evaluator = Callable[[StrategySpec, str], WindowStats]
"""``evaluator(spec, window)`` with window ``early``, ``later`` or ``other`` (the other market)."""


@dataclass(frozen=True, slots=True)
class Child:
    spec: StrategySpec
    label: str
    parent_hash: str
    generation: int
    early: WindowStats
    later: WindowStats | None = None
    other: WindowStats | None = None

    @property
    def hash(self) -> str:
        return spec_hash(self.spec)


@dataclass(slots=True)
class GenerationReport:
    generation: int
    parents: int
    children_evaluated: int
    survivors: int
    trials_so_far: int


@dataclass(slots=True)
class SearchOutcome:
    generations: list[GenerationReport] = field(default_factory=list)
    survivors: list[Child] = field(default_factory=list)
    trial_hashes: set[str] = field(default_factory=set)
    evaluated: list[Child] = field(default_factory=list)

    @property
    def trials(self) -> int:
        return len(self.trial_hashes)


def spec_hash(spec: StrategySpec) -> str:
    return strategy_hash(spec.model_dump(mode="json"))


def _replace_exits(spec: StrategySpec, stop: float, target: float) -> StrategySpec:
    rules = [rule for rule in spec.exit_rules if rule.kind not in {"stop", "target"}]
    rules = [
        StopExit(kind="stop", distance=stop, unit="atr"),
        TargetExit(kind="target", distance=target, unit="atr"),
        *rules,
    ]
    return spec.model_copy(update={"exit_rules": tuple(rules)})


def _replace_time_stop(spec: StrategySpec, bars: int) -> StrategySpec:
    rules: list[Any] = [rule for rule in spec.exit_rules if rule.kind != "time_stop"]
    rules.append(TimeStopExit(kind="time_stop", bars=bars))
    return spec.model_copy(update={"exit_rules": tuple(rules)})


def _trend_condition(long_side: bool, with_trend: bool) -> IndicatorCondition:
    fast_above = long_side == with_trend  # long with trend, or short against it
    return IndicatorCondition(
        indicator="ema",
        period=50,
        operator="gt" if fast_above else "lt",
        threshold=0.0,
        threshold_indicator="ema",
        threshold_period=200,
    )


def mutations(spec: StrategySpec) -> list[tuple[str, StrategySpec]]:
    """One-change children of ``spec`` from the fixed menu (deduplicated, bounded)."""
    seen = {spec_hash(spec)}
    children: list[tuple[str, StrategySpec]] = []

    def add(label: str, child: StrategySpec) -> None:
        digest = spec_hash(child)
        if digest not in seen and len(children) < MAX_CHILDREN_PER_PARENT:
            seen.add(digest)
            children.append((label, child))

    for stop, target in STOP_TARGETS:
        add(f"exit_atr_{stop:g}_{target:g}", _replace_exits(spec, stop, target))
    for bars in TIME_STOPS:
        add(f"time_stop_{bars}", _replace_time_stop(spec, bars))
    for sessions in SESSION_SETS:
        name = "+".join(sessions) if sessions else "any"
        add(f"session_{name}", spec.model_copy(update={"session_filters": tuple(sessions)}))
    long_side = spec.direction == "long"
    if len(spec.entry_conditions) < MAX_CONDITIONS:
        for with_trend in (True, False):
            condition = _trend_condition(long_side, with_trend)
            if condition in spec.entry_conditions:
                continue
            label = "trend_with" if with_trend else "trend_against"
            add(
                label,
                spec.model_copy(update={"entry_conditions": (*spec.entry_conditions, condition)}),
            )
    for regime in VOLATILITY_REGIMES:
        add(f"volatility_{regime}", spec.model_copy(update={"regime_filters": (regime,)}))
    return children


def window_evaluator(
    windows: Mapping[str, Sequence[Candle]],
    cost_model: CostModel,
    *,
    other_instrument: str | None = None,
) -> Evaluator:
    """Real-engine evaluator: net/gross per trade after costs on a fixed candle window."""
    caches: dict[tuple[str, int], IndicatorCache] = {}

    def evaluate(spec: StrategySpec, window: str) -> WindowStats:
        candles = windows[window]
        target = spec
        if window == "other":
            if other_instrument is None:
                raise ValueError("an 'other' window needs other_instrument")
            target = spec.model_copy(update={"instrument": other_instrument})
        key = (window, spec.timeframe_minutes)
        if key not in caches:
            caches[key] = IndicatorCache.from_candles(candles, spec.timeframe_minutes)
        result = run_backtest(target, candles, cost_model=cost_model, indicator_cache=caches[key])
        count = len(result.trades)
        if count == 0:
            return WindowStats(0, 0.0, 0.0)
        return WindowStats(count, result.net_pnl / count, result.gross_pnl / count)

    return evaluate


def split_windows(
    home: Sequence[Candle],
    other: Sequence[Candle],
    *,
    early_fraction: float = EARLY_FRACTION,
) -> dict[str, Sequence[Candle]]:
    """Early/later windows of the home market and the other market's later window.

    The boundary is a timestamp, so both markets are cut at the same moment.  Only training
    candles may be passed in: the pipeline's out-of-sample slice and the holdout stay unseen.
    """
    if not 0.2 <= early_fraction <= 0.8:
        raise ValueError("early_fraction must be between 0.2 and 0.8")
    cut = home[max(1, int(len(home) * early_fraction))].ts_utc
    return {
        "early": [c for c in home if c.ts_utc < cut],
        "later": [c for c in home if c.ts_utc >= cut],
        "other": [c for c in other if c.ts_utc >= cut],
    }


class StagedSearch:
    def __init__(
        self,
        evaluator: Evaluator,
        *,
        min_trades: int = MIN_WINDOW_TRADES,
        max_generations: int = MAX_GENERATIONS,
        max_parents: int = 10,
        max_survivors_per_parent: int = 5,
        progress: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        prior_hashes: set[str] | None = None,
    ) -> None:
        if not 1 <= max_generations <= MAX_GENERATIONS:
            raise ValueError("max_generations must be between 1 and 3")
        self.evaluator = evaluator
        self.min_trades = min_trades
        self.max_generations = max_generations
        self.max_parents = max_parents
        self.max_survivors_per_parent = max_survivors_per_parent
        self.progress = progress or (lambda _message: None)
        self.should_stop = should_stop or (lambda: False)
        self.prior_hashes = set(prior_hashes or ())

    def _survives(self, child: Child) -> bool:
        for stats in (child.early, child.later, child.other):
            if stats is None or stats.trades < self.min_trades or stats.net_per_trade <= 0:
                return False
        return True

    def run(self, parents: Sequence[StrategySpec]) -> SearchOutcome:
        outcome = SearchOutcome(trial_hashes=set(self.prior_hashes))
        evaluated_hashes: set[str] = set()
        frontier = list(parents)[: self.max_parents]
        for spec in frontier:
            outcome.trial_hashes.add(spec_hash(spec))
            evaluated_hashes.add(spec_hash(spec))
        for generation in range(1, self.max_generations + 1):
            if not frontier or self.should_stop():
                break
            survivors_by_parent: dict[str, list[Child]] = {}
            evaluated_here = 0
            for number, parent in enumerate(frontier, start=1):
                parent_hash = spec_hash(parent)
                evaluated_hashes.add(parent_hash)
                for label, spec in mutations(parent):
                    digest = spec_hash(spec)
                    if digest in evaluated_hashes:
                        continue  # already evaluated in this search; never counted twice
                    evaluated_hashes.add(digest)
                    if self.should_stop():
                        break
                    outcome.trial_hashes.add(digest)
                    evaluated_here += 1
                    early = self.evaluator(spec, "early")
                    child = Child(spec, label, parent_hash, generation, early)
                    outcome.evaluated.append(child)
                    if early.trades < self.min_trades or early.net_per_trade <= 0:
                        continue
                    later = self.evaluator(spec, "later")
                    child = Child(spec, label, parent_hash, generation, early, later)
                    if later.trades < self.min_trades or later.net_per_trade <= 0:
                        continue
                    other = self.evaluator(spec, "other")
                    child = Child(spec, label, parent_hash, generation, early, later, other)
                    if self._survives(child):
                        survivors_by_parent.setdefault(parent_hash, []).append(child)
                self.progress(
                    f"generation {generation}: parent {number}/{len(frontier)}, "
                    f"{evaluated_here} variants, {outcome.trials} trials"
                )
            generation_survivors: list[Child] = []
            for children in survivors_by_parent.values():
                children.sort(key=lambda c: c.early.net_per_trade, reverse=True)
                generation_survivors.extend(children[: self.max_survivors_per_parent])
            outcome.survivors.extend(generation_survivors)
            outcome.generations.append(
                GenerationReport(
                    generation,
                    len(frontier),
                    evaluated_here,
                    len(generation_survivors),
                    outcome.trials,
                )
            )
            generation_survivors.sort(
                key=lambda c: min(
                    c.later.net_per_trade if c.later else 0.0,
                    c.other.net_per_trade if c.other else 0.0,
                ),
                reverse=True,
            )
            frontier = [child.spec for child in generation_survivors[: self.max_parents]]
        return outcome


def child_payload(child: Child) -> dict[str, Any]:
    def stats(value: WindowStats | None) -> dict[str, float | int] | None:
        return (
            None
            if value is None
            else {
                "trades": value.trades,
                "net_per_trade": round(value.net_per_trade, 4),
                "gross_per_trade": round(value.gross_per_trade, 4),
            }
        )

    return {
        "hash": child.hash,
        "parent": child.parent_hash,
        "generation": child.generation,
        "change": child.label,
        "early": stats(child.early),
        "later": stats(child.later),
        "other_market": stats(child.other),
    }
