"""Strategy Lab job runner and the read-only Telegram job facade.

The runner owns orchestration only.  It reads HistData Parquet, delegates
backtests and Gate 1 to the existing lab modules, and writes append-only
attempt records/results under the workspace.  It never imports broker or order
services and never promotes a strategy.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import multiprocessing
import os
import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

from trader.data.resample import Candle, resample_ohlc
from trader.lab.backtest import CostModel
from trader.lab.ledger import ExperimentLedger, ExperimentRecord
from trader.lab.miner import PatternMiner
from trader.lab.pipeline import ValidationPipeline
from trader.lab.seeds import seed_specs
from trader.lab.spec import StrategySpec
from trader.lab.validator import HoldoutReader, strategy_hash
from trader.viz import strategy_comparison

LOGGER = logging.getLogger(__name__)
INSTRUMENTS = ("NSXUSD", "XAUUSD")
TIMEFRAMES = (1, 3, 5)
Timeframe = Literal[1, 3, 5]


@dataclass(frozen=True, slots=True)
class Coverage:
    instrument: str
    first_bar: datetime | None
    last_bar: datetime | None
    months: int
    gaps: int
    qa_flags: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "first_bar": self.first_bar.isoformat() if self.first_bar else None,
            "last_bar": self.last_bar.isoformat() if self.last_bar else None,
            "months": self.months,
            "gaps": self.gaps,
            "qa_flags": list(self.qa_flags),
        }


@dataclass(frozen=True, slots=True)
class LabAttempt:
    strategy_hash: str
    instrument: str
    timeframe: int
    source: str
    verdict: str
    reason: str = ""
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "strategy_hash": self.strategy_hash,
            "instrument": self.instrument,
            "timeframe": self.timeframe,
            "source": self.source,
            "verdict": self.verdict,
            "reason": self.reason[:160],
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class LabRunResult:
    run_id: str
    started_at: datetime
    finished_at: datetime
    attempts: tuple[LabAttempt, ...]
    survivors: tuple[LabAttempt, ...]
    coverage: tuple[Coverage, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "attempts": [item.payload() for item in self.attempts],
            "survivors": [item.payload() for item in self.survivors],
            "coverage": [item.payload() for item in self.coverage],
        }


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def _parquet_files(root: Path, instrument: str) -> tuple[Path, ...]:
    return tuple(sorted((root / instrument / "1m").glob("**/*.parquet")))


def load_histdata_candles(
    root: str | Path, instrument: str, timeframe: int = 1
) -> tuple[Candle, ...]:
    """Read HistData M1 bars and optionally build complete 3m/5m bars."""
    if timeframe not in TIMEFRAMES:
        raise ValueError("timeframe must be one of 1, 3 or 5 minutes")
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pyarrow is required for Strategy Lab data") from exc
    rows: list[Candle] = []
    for path in _parquet_files(Path(root), instrument):
        for row in pq.read_table(path).to_pylist():
            try:
                candle = Candle(
                    ts_utc=_as_utc(row["ts_utc"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0) or 0.0),
                    series_key=(instrument, "histdata"),
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if all(
                math.isfinite(value)
                for value in (candle.open, candle.high, candle.low, candle.close)
            ):
                rows.append(candle)
    rows.sort(key=lambda item: item.ts_utc)
    unique = {item.ts_utc: item for item in rows}
    minute = tuple(unique.values())
    if timeframe == 1:
        return minute
    return tuple(item for item in resample_ohlc(minute, timeframe) if item.complete)


def coverage_report(root: str | Path, instrument: str) -> Coverage:
    """Summarize lake coverage and QA sidecars without opening broker state."""
    candles = load_histdata_candles(root, instrument)
    files = _parquet_files(Path(root), instrument)
    qa_flags: set[str] = set()
    lineage = Path(root).parent.parent / "data_lineage"
    for path in lineage.glob(f"{instrument}_*_qa.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("status") != "ok":
                qa_flags.add(str(report.get("status", "qa_error"))[:32])
            if report.get("gaps"):
                qa_flags.add("gaps")
        except (OSError, ValueError, TypeError):
            qa_flags.add("qa_unreadable")
    first = candles[0].ts_utc if candles else None
    last = candles[-1].ts_utc if candles else None
    months = (
        0
        if first is None or last is None
        else (last.year - first.year) * 12 + last.month - first.month + 1
    )
    gaps = max(0, len(files) - months) if files and months else 0
    return Coverage(instrument, first, last, months, gaps, tuple(sorted(qa_flags)))


def pessimistic_cost_model(config_path: str | Path = "config/strategy_lab.yaml") -> CostModel:
    """Load pessimistic costs; calibration is intentionally not assumed."""
    import yaml

    payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    defaults = payload.get("cost_defaults", {})
    spread = defaults.get("spread_points", {})
    slippage = defaults.get("slippage_points", {})
    return CostModel(
        spread_points={str(k): float(v) for k, v in spread.items()},
        slippage_points={str(k): float(v) for k, v in slippage.items()},
        provisional=True,
    )


def _holdout_loader(
    _raw: bytes, *, root: Path, instrument: str, timeframe: int
) -> tuple[Candle, ...]:
    return load_histdata_candles(root, instrument, timeframe)


def tune_variants(spec: StrategySpec, *, limit: int = 16) -> tuple[StrategySpec, ...]:
    """Return deterministic bounded variants for a declarative strategy.

    One or two parameters use a small Cartesian grid.  Larger parameter sets
    use a seeded pseudo-random sample, keeping all variants reproducible and
    ensuring every trial can be counted by Gate 1's correction.
    """
    parameters = spec.tunable_parameters
    if not parameters:
        return (spec,)
    values: list[list[float]] = []
    for parameter in parameters:
        values.append([float(parameter.minimum), float(parameter.value), float(parameter.maximum)])
    combinations: list[tuple[float, ...]] = []
    if len(parameters) <= 2:
        import itertools

        combinations = list(itertools.product(*values))[:limit]
    else:
        import random

        generator = random.Random(0)
        for _ in range(limit):
            combinations.append(
                tuple(generator.uniform(item.minimum, item.maximum) for item in parameters)
            )
    variants: list[StrategySpec] = []
    for combination in combinations:
        updates = {
            "tunable_parameters": tuple(
                parameter.model_copy(update={"value": value})
                for parameter, value in zip(parameters, combination, strict=True)
            )
        }
        variants.append(spec.model_copy(update=updates))
    return tuple(variants)


def _worker_prepare(item: tuple[dict[str, Any], str]) -> tuple[str, str]:
    """Spawn-safe worker boundary; malformed work becomes an error attempt."""
    spec, source = item
    try:
        digest = strategy_hash(spec)
        return digest, source
    except Exception as exc:  # noqa: BLE001
        return "", f"error:{type(exc).__name__}"


class LabRunner:
    def __init__(
        self,
        *,
        lake_root: str | Path = "/workspace/data/lake",
        holdout_root: str | Path = "/workspace/data/holdout",
        ledger: ExperimentLedger | None = None,
        config_path: str | Path = "config/strategy_lab.yaml",
        idea_generator: Any | None = None,
    ) -> None:
        self.lake_root = Path(lake_root)
        self.holdout_root = Path(holdout_root).resolve()
        self.ledger = ledger or ExperimentLedger()
        self.config_path = Path(config_path)
        self.idea_generator = idea_generator

    def _candidates(
        self, instrument: str, timeframe: int, sources: Sequence[str], candles: Sequence[Candle]
    ) -> list[tuple[str, Any]]:
        result: list[tuple[str, Any]] = []
        if "seeds" in sources or "all" in sources:
            for seed, spec in seed_specs(instrument, timeframe):
                result.extend((seed.name, variant) for variant in tune_variants(spec))
        if ("patterns" in sources or "pattern" in sources or "all" in sources) and candles:
            mined = PatternMiner(
                instrument=instrument,
                timeframe_minutes=cast(Timeframe, timeframe),
                ledger=self.ledger,
            ).scan(candles)
            result.extend((candidate.name, candidate.strategy) for candidate in mined.candidates)
        if ("llm" in sources or "idea_generator" in sources) and self.idea_generator is not None:
            try:
                generated = asyncio.run(
                    self.idea_generator.generate(
                        instrument=instrument,
                        summary={"bars": len(candles), "timeframe_minutes": timeframe},
                    )
                )
                if generated is not None:
                    result.append(("idea_generator", generated))
            except Exception as exc:  # noqa: BLE001 - optional source is fail-soft
                LOGGER.warning("idea generator failed (%s)", type(exc).__name__)
        return result

    def run(
        self,
        *,
        instruments: Sequence[str] = INSTRUMENTS,
        timeframes: Sequence[int] = TIMEFRAMES,
        idea_sources: Sequence[str] = ("seeds", "patterns"),
        max_ideas: int = 100,
        workers: int | None = None,
        force: bool = False,
        run_id: str | None = None,
    ) -> LabRunResult:
        started = datetime.now(UTC)
        run_name = run_id or started.strftime("%Y%m%dT%H%M%SZ")
        workers = max(1, workers if workers is not None else (os.cpu_count() or 2) - 2)
        coverage = tuple(coverage_report(self.lake_root, instrument) for instrument in instruments)
        attempts: list[LabAttempt] = []

        def record_attempt(digest: str, source: str, metrics: Mapping[str, Any]) -> None:
            now = datetime.now(UTC)
            self.ledger.append(
                ExperimentRecord(
                    strategy_hash=digest,
                    parameters={"run_id": run_name, "source": source},
                    data_start=now,
                    data_end=now,
                    metrics=dict(metrics),
                    proposer="lab_runner",
                )
            )

        for instrument, timeframe in ((i, t) for i in instruments for t in timeframes):
            candles = load_histdata_candles(self.lake_root, instrument, timeframe)
            if len(candles) < 10:
                continue
            candidates = self._candidates(instrument, timeframe, idea_sources, candles)[:max_ideas]
            prepared = [(spec.model_dump(mode="json"), source) for source, spec in candidates]
            # Spawn workers for the deterministic preparation boundary.  All file and ledger I/O
            # remains in the parent, so a worker crash cannot abort a run.
            worker_results: list[tuple[str, str]] = []
            if workers > 1 and len(prepared) > 1:
                try:
                    with ProcessPoolExecutor(
                        max_workers=workers, mp_context=multiprocessing.get_context("spawn")
                    ) as pool:
                        futures = [pool.submit(_worker_prepare, item) for item in prepared]
                        for future in as_completed(futures):
                            try:
                                worker_results.append(future.result())
                            except Exception as exc:  # noqa: BLE001
                                worker_results.append(("", f"error:{type(exc).__name__}"))
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("lab worker pool failed (%s)", type(exc).__name__)
                    worker_results = [("", f"error:{type(exc).__name__}") for _ in prepared]
            else:
                worker_results = [_worker_prepare(item) for item in prepared]
            by_hash = {digest: source for digest, source in worker_results if digest}
            holdout = next(self.holdout_root.glob("**/*.parquet"), None)
            if holdout is None:
                for digest, source in worker_results:
                    if digest:
                        record_attempt(
                            digest, source, {"verdict": "fail", "reason": "holdout_missing"}
                        )
                        attempts.append(
                            LabAttempt(
                                digest, instrument, timeframe, source, "fail", "holdout_missing"
                            )
                        )
                continue
            reader = HoldoutReader(self.holdout_root, ledger=self.ledger)
            pipeline = ValidationPipeline(
                ledger=self.ledger, cost_model=pessimistic_cost_model(self.config_path)
            )
            split = max(1, int(len(candles) * 0.8))
            train, test = candles[:split], candles[split:]
            if not test:
                continue
            specs = [
                spec
                for source, spec in candidates
                if force
                or self.ledger.count_for_strategy(strategy_hash(spec.model_dump(mode="json"))) == 0
            ]
            try:
                decisions = pipeline.run(
                    specs,
                    train,
                    out_of_sample=test,
                    holdout_reader=reader,
                    holdout_path=holdout,
                    holdout_loader=partial(
                        _holdout_loader,
                        root=self.holdout_root,
                        instrument=instrument,
                        timeframe=timeframe,
                    ),
                    train_bars=max(1, len(train) // 2),
                    test_bars=max(1, len(train) // 5),
                    step_bars=max(1, len(train) // 5),
                )
            except Exception as exc:  # noqa: BLE001
                for digest, source in worker_results:
                    if digest:
                        record_attempt(
                            digest, source, {"verdict": "error", "reason": type(exc).__name__}
                        )
                        attempts.append(
                            LabAttempt(
                                digest, instrument, timeframe, source, "error", type(exc).__name__
                            )
                        )
                continue
            decision_hashes = {decision.strategy_hash: decision for decision in decisions}
            for source, spec in candidates:
                digest = strategy_hash(spec.model_dump(mode="json"))
                if digest not in by_hash:
                    record_attempt(digest, source, {"verdict": "error", "reason": "worker_failed"})
                    attempts.append(
                        LabAttempt(digest, instrument, timeframe, source, "error", "worker_failed")
                    )
                    continue
                if (
                    not force
                    and self.ledger.count_for_strategy(digest) > 0
                    and digest not in decision_hashes
                ):
                    record_attempt(
                        digest, source, {"verdict": "skipped", "reason": "duplicate_strategy_hash"}
                    )
                    attempts.append(
                        LabAttempt(
                            digest,
                            instrument,
                            timeframe,
                            source,
                            "skipped",
                            "duplicate_strategy_hash",
                        )
                    )
                    continue
                decision = decision_hashes.get(digest)
                if decision is None:
                    record_attempt(
                        digest, source, {"verdict": "fail", "reason": "validation_failed"}
                    )
                    attempts.append(
                        LabAttempt(
                            digest, instrument, timeframe, source, "fail", "validation_failed"
                        )
                    )
                    continue
                verdict = "pass" if decision.promoted else "fail"
                reason = ",".join(decision.validation.reasons)
                record_attempt(
                    digest, source, {"verdict": verdict, "reason": reason, **decision.metrics}
                )
                attempts.append(
                    LabAttempt(
                        digest, instrument, timeframe, source, verdict, reason, decision.metrics
                    )
                )
        finished = datetime.now(UTC)
        survivors = tuple(item for item in attempts if item.verdict == "pass")
        return LabRunResult(run_name, started, finished, tuple(attempts), survivors, coverage)

    @staticmethod
    def format_results(result: LabRunResult, *, max_lines: int = 12) -> str:
        lines = [
            f"Strategy Lab {result.run_id}",
            "id instrument tf trades oos_pf holdout_pf max_dd verdict reason",
        ]
        for item in sorted(
            result.attempts,
            key=lambda value: (value.verdict != "pass", value.instrument, value.timeframe),
        )[: max(0, max_lines - 2)]:
            lines.append(
                (
                    f"{item.strategy_hash[:10]} {item.instrument} {item.timeframe}m "
                    f"{item.metrics.get('trades', '-')} "
                    f"{item.metrics.get('out_of_sample_profit_factor', '-')} "
                    f"{item.metrics.get('holdout_profit_factor', '-')} "
                    f"{item.metrics.get('backtest_drawdown_pct', '-')} "
                    f"{item.verdict} {item.reason[:32]}"
                ).rstrip()
            )
        return "\n".join(lines)[:3900]


class LabJobManager:
    """One-run-at-a-time background facade used by Telegram ``/lab``."""

    def __init__(self, runner: LabRunner | None = None) -> None:
        self.runner = runner or LabRunner()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._result: LabRunResult | None = None
        self._error: str | None = None

    def handle(self, command: str) -> str:
        parts = command.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "status"
        if action == "run":
            instrument_arg = parts[2].lower() if len(parts) > 2 else "all"
            instrument_map = {
                "gold": ("XAUUSD",),
                "nas100": ("NSXUSD",),
                "all": INSTRUMENTS,
            }
            instruments = instrument_map.get(instrument_arg)
            if instruments is None:
                return "Usage: /lab run [gold|nas100|all]"
            with self._lock:
                if self._thread is not None and self._thread.is_alive():
                    return "Strategy Lab: a run is already in progress."
                self._error = None
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._run,
                    args=(instruments,),
                    daemon=True,
                    name="strategy-lab",
                )
                self._thread.start()
            return "Strategy Lab run started."
        if action == "status":
            return (
                "Strategy Lab: run in progress."
                if self._thread and self._thread.is_alive()
                else ("Strategy Lab: ready." if self._result is None else self._summary())
            )
        if action == "results":
            return self._summary()
        if action == "stop":
            self._stop.set()
            return "Strategy Lab stop requested."
        if action == "chart":
            return "Strategy Lab chart is available only for a completed survivor."
        return (
            "Usage: /lab run [gold|nas100|all], /lab status, /lab results, "
            "/lab chart <id>, /lab stop"
        )

    def _run(self, instruments: Sequence[str]) -> None:
        try:
            self._result = self.runner.run(instruments=instruments)
        except Exception as exc:  # noqa: BLE001
            self._error = type(exc).__name__
            LOGGER.warning("strategy lab run failed (%s)", type(exc).__name__)

    def _summary(self) -> str:
        if self._error:
            return "Strategy Lab: run failed safely."
        if self._result is None:
            return "Strategy Lab: no completed run."
        return LabRunner.format_results(self._result)

    def chart(self, _command: str, argument: str = "") -> bytes:
        """Render a bounded survivor comparison chart for Telegram."""
        if self._result is None or not self._result.survivors:
            raise RuntimeError("no Strategy Lab survivor chart is available")
        requested = argument.split(maxsplit=1)[1] if len(argument.split()) > 1 else ""
        rows = [
            item
            for item in self._result.survivors
            if not requested or item.strategy_hash.startswith(requested)
        ]
        if not rows:
            raise RuntimeError("requested Strategy Lab survivor was not found")
        figure = strategy_comparison(
            [
                {"strategy": item.strategy_hash[:10], "pnl": item.metrics.get("net_pnl", 0.0)}
                for item in rows[:3]
            ],
            title="Strategy Lab survivors",
        )
        buffer = io.BytesIO()
        try:
            figure.savefig(buffer, format="png", dpi=120)
            return buffer.getvalue()
        finally:
            clear = getattr(figure, "clear", None)
            if callable(clear):
                clear()


__all__ = [
    "Coverage",
    "LabAttempt",
    "LabJobManager",
    "LabRunResult",
    "LabRunner",
    "coverage_report",
    "load_histdata_candles",
    "pessimistic_cost_model",
    "tune_variants",
]
