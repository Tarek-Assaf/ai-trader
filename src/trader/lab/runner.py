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
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
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
from trader.lab.validator import open_sealed_reader, strategy_hash
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


def _result_from_payload(payload: Mapping[str, Any]) -> LabRunResult:
    def stamp(value: Any) -> datetime:
        return _as_utc(value)

    attempts = tuple(
        LabAttempt(
            item["strategy_hash"],
            item["instrument"],
            int(item["timeframe"]),
            item["source"],
            item["verdict"],
            item.get("reason", ""),
            item.get("metrics", {}),
        )
        for item in payload.get("attempts", [])
    )
    coverage = tuple(
        Coverage(
            item["instrument"],
            stamp(item["first_bar"]) if item.get("first_bar") else None,
            stamp(item["last_bar"]) if item.get("last_bar") else None,
            int(item["months"]),
            int(item["gaps"]),
            tuple(item.get("qa_flags", [])),
        )
        for item in payload.get("coverage", [])
    )
    return LabRunResult(
        str(payload["run_id"]),
        stamp(payload["started_at"]),
        stamp(payload["finished_at"]),
        attempts,
        tuple(item for item in attempts if item.verdict == "pass"),
        coverage,
    )


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


def _path_month(path: Path) -> tuple[int, int] | None:
    try:
        year = int(path.parent.name)
        month = int(path.stem.split(".", 1)[0])
    except (TypeError, ValueError):
        return None
    return (year, month) if 1 <= month <= 12 else None


def holdout_months(
    root: str | Path, instruments: Sequence[str] = INSTRUMENTS
) -> set[tuple[int, int]]:
    """Return calendar months copied into the sealed holdout directory."""
    result: set[tuple[int, int]] = set()
    for instrument in instruments:
        result.update(
            month for path in _parquet_files(Path(root), instrument) if (month := _path_month(path))
        )
    return result


def prepare_holdout(
    lake_root: str | Path,
    holdout_root: str | Path,
    *,
    instruments: Sequence[str] = INSTRUMENTS,
    months: int = 2,
) -> tuple[Path, ...]:
    """Copy the newest calendar months into the sealed holdout tree.

    The copy is explicit and repeatable; the runner will refuse to tune against
    those months while the validator alone reads the destination.
    """
    if months < 1:
        raise ValueError("holdout months must be positive")
    source_root, destination_root = Path(lake_root), Path(holdout_root)
    manifest: dict[str, list[str]] = {}
    selected_by_instrument: dict[str, set[tuple[int, int]]] = {}
    for instrument in instruments:
        files = _parquet_files(source_root, instrument)
        keyed = [(key, path) for path in files if (key := _path_month(path)) is not None]
        selected_months = set(sorted({key for key, _ in keyed})[-months:])
        selected_by_instrument[instrument] = selected_months
        manifest[instrument] = [
            f"{year:04d}-{month:02d}" for year, month in sorted(selected_months)
        ]

    manifest_path = destination_root / "manifest.json"
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("existing holdout manifest is unreadable") from exc
        if existing.get("months") != months or existing.get("instruments") != manifest:
            raise ValueError("holdout is already prepared with a different month set")
        return tuple(sorted(destination_root.glob("**/*.parquet")))

    copied: list[Path] = []
    for instrument in instruments:
        files = _parquet_files(source_root, instrument)
        keyed = [(key, path) for path in files if (key := _path_month(path)) is not None]
        selected_months = selected_by_instrument[instrument]
        for key, path in keyed:
            if key not in selected_months:
                continue
            relative = path.relative_to(source_root / instrument)
            destination = destination_root / instrument / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied.append(destination)
    destination_root.mkdir(parents=True, exist_ok=True)
    (destination_root / "manifest.json").write_text(
        json.dumps({"months": months, "instruments": manifest}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return tuple(copied)


def load_histdata_candles(
    root: str | Path,
    instrument: str,
    timeframe: int = 1,
    *,
    exclude_months: set[tuple[int, int]] | None = None,
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
                if (
                    exclude_months is None
                    or (candle.ts_utc.year, candle.ts_utc.month) not in exclude_months
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
    month_keys = {month for path in files if (month := _path_month(path)) is not None}
    gaps = max(0, months - len(month_keys)) if files and months else 0
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
        minimum, maximum, current = (
            float(parameter.minimum),
            float(parameter.maximum),
            float(parameter.value),
        )
        # Five deterministic points give each parameter a meaningful
        # neighbourhood check; three points (min/current/max) are too sparse
        # for Gate 1's stability requirement.
        quarter = minimum + (maximum - minimum) * 0.25
        three_quarter = minimum + (maximum - minimum) * 0.75
        values.append(list(dict.fromkeys((minimum, quarter, current, three_quarter, maximum))))
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
                result.extend((f"seed:{seed.name}", variant) for variant in tune_variants(spec))
        if ("patterns" in sources or "pattern" in sources or "all" in sources) and candles:
            mined = PatternMiner(
                instrument=instrument,
                timeframe_minutes=cast(Timeframe, timeframe),
                ledger=self.ledger,
            ).scan(candles)
            result.extend(("pattern", candidate.strategy) for candidate in mined.candidates)
        if ("llm" in sources or "idea_generator" in sources) and self.idea_generator is not None:
            try:
                generated = asyncio.run(
                    self.idea_generator.generate(
                        instrument=instrument,
                        summary={"bars": len(candles), "timeframe_minutes": timeframe},
                    )
                )
                if generated is not None:
                    result.append(("llm", generated))
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
        stop_event: Any | None = None,
        stop_file: str | Path | None = None,
        progress_path: str | Path | None = None,
    ) -> LabRunResult:
        started = datetime.now(UTC)
        run_name = run_id or started.strftime("%Y%m%dT%H%M%SZ")
        workers = max(1, workers if workers is not None else (os.cpu_count() or 2) - 2)
        coverage = tuple(coverage_report(self.lake_root, instrument) for instrument in instruments)
        attempts: list[LabAttempt] = []
        holdout_files = tuple(self.holdout_root.glob("**/*.parquet"))
        if not holdout_files:
            raise RuntimeError(
                "Strategy Lab holdout is absent; run lab_data_check.py --prepare-holdout first"
            )
        sealed_months = holdout_months(self.holdout_root, instruments)

        def stopped() -> bool:
            return bool(
                (stop_event is not None and stop_event.is_set())
                or (stop_file is not None and Path(stop_file).exists())
            )

        class StopSignal:
            def is_set(self) -> bool:
                return stopped()

        pipeline_stop = StopSignal()

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

        groups: list[
            tuple[
                str,
                int,
                tuple[Candle, ...],
                tuple[Candle, ...],
                list[tuple[str, Any]],
                list[StrategySpec],
                Path,
            ]
        ] = []
        for instrument, timeframe in ((i, t) for i in instruments for t in timeframes):
            candles = load_histdata_candles(
                self.lake_root,
                instrument,
                timeframe,
                exclude_months=sealed_months,
            )
            if len(candles) < 10:
                continue
            split = max(1, int(len(candles) * 0.8))
            train, test = candles[:split], candles[split:]
            if not test:
                continue
            # Idea mining and parameter tuning must never inspect the later
            # evaluation window. The sealed holdout has already been excluded
            # from ``candles`` above, so this is the remaining in-sample slice.
            grouped = self._candidates(instrument, timeframe, idea_sources, train)
            by_source: dict[str, list[tuple[str, Any]]] = {}
            for source, spec in grouped:
                family = source.split(":", 1)[0]
                by_source.setdefault(family, []).append((source, spec))
            candidates = [item for source in by_source.values() for item in source[:max_ideas]]
            holdout = next(
                (
                    path
                    for path in holdout_files
                    if path.parts[-3:-1] == (instrument, "1m") or instrument in path.parts
                ),
                holdout_files[0],
            )
            specs = [
                spec
                for source, spec in candidates
                if force
                or self.ledger.count_for_strategy(strategy_hash(spec.model_dump(mode="json"))) == 0
            ]
            groups.append(
                (instrument, timeframe, tuple(train), tuple(test), candidates, specs, holdout)
            )

        existing_hashes = {
            item.strategy_hash
            for item in self.ledger.records()
            if item.parameters.get("pipeline")
            not in {"neighbor", "gate1_screen", "gate1_final", "worker"}
        }
        current_hashes = {
            strategy_hash(spec.model_dump(mode="json"))
            for _, _, _, _, candidates, _, _ in groups
            for _, spec in candidates
        }
        trial_count = max(1, len(existing_hashes | current_hashes))
        total_ideas = sum(len(group[4]) for group in groups)
        completed_ideas = 0

        def write_progress(status: str = "running") -> None:
            if progress_path is None:
                return
            target = Path(progress_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(
                    {
                        "status": status,
                        "run_id": run_name,
                        "ideas_done": completed_ideas,
                        "ideas_total": total_ideas,
                        "survivors": sum(item.verdict == "pass" for item in attempts),
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

        write_progress()
        for instrument, timeframe, train, test, candidates, specs, holdout in groups:
            if stopped():
                break
            reader = open_sealed_reader(self.holdout_root, self.ledger)
            pipeline = ValidationPipeline(
                ledger=self.ledger, cost_model=pessimistic_cost_model(self.config_path)
            )
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
                    workers=workers,
                    stop_event=pipeline_stop,
                    trials=trial_count,
                )
            except Exception as exc:  # noqa: BLE001
                for source, spec in candidates:
                    digest = strategy_hash(spec.model_dump(mode="json"))
                    record_attempt(
                        digest, source, {"verdict": "error", "reason": type(exc).__name__}
                    )
                    attempts.append(
                        LabAttempt(
                            digest, instrument, timeframe, source, "error", type(exc).__name__
                        )
                    )
                completed_ideas += len(candidates)
                write_progress()
                continue
            decision_hashes = {decision.strategy_hash: decision for decision in decisions}
            for source, spec in candidates:
                digest = strategy_hash(spec.model_dump(mode="json"))
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
                is_worker_error = decision.metrics.get("verdict") == "error"
                verdict = "error" if is_worker_error else ("pass" if decision.promoted else "fail")
                reason = ",".join(decision.validation.reasons)
                record_attempt(
                    digest, source, {"verdict": verdict, "reason": reason, **decision.metrics}
                )
                attempts.append(
                    LabAttempt(
                        digest, instrument, timeframe, source, verdict, reason, decision.metrics
                    )
                )
            completed_ideas += len(candidates)
            write_progress()
        finished = datetime.now(UTC)
        survivors = tuple(item for item in attempts if item.verdict == "pass")
        write_progress("completed")
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
    """One-run-at-a-time facade; production runs in a separate process."""

    def __init__(
        self,
        runner: LabRunner | Any | None = None,
        *,
        state_root: str | Path = "/workspace/state/lab",
    ) -> None:
        self.runner = runner or LabRunner()
        self.state_root = Path(state_root)
        self.results_path = self.state_root / "results.json"
        self.progress_path = self.state_root / "progress.json"
        self.stop_path = self.state_root / "stop"
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._log_handle: Any | None = None
        self._stop = threading.Event()
        self._result: LabRunResult | None = None
        self._error: str | None = None

    def handle(self, command: str) -> str:
        parts = command.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "status"
        if action == "run":
            instrument_arg = parts[2].lower() if len(parts) > 2 else "all"
            instrument_map = {"gold": ("XAUUSD",), "nas100": ("NSXUSD",), "all": INSTRUMENTS}
            instruments = instrument_map.get(instrument_arg)
            if instruments is None:
                return "Usage: /lab run [gold|nas100|all]"
            with self._lock:
                if (self._thread and self._thread.is_alive()) or (
                    self._process and self._process.poll() is None
                ):
                    return "Strategy Lab: a run is already in progress."
                self._error = None
                self._stop.clear()
                self.stop_path.unlink(missing_ok=True)
                if isinstance(self.runner, LabRunner):
                    self.state_root.mkdir(parents=True, exist_ok=True)
                    script = Path(__file__).resolve().parents[3] / "scripts" / "lab_run.py"
                    log_path = self.state_root / "run.log"
                    self._log_handle = log_path.open("a", encoding="utf-8")
                    try:
                        self._process = subprocess.Popen(
                            [
                                sys.executable,
                                str(script),
                                "--lake",
                                str(self.runner.lake_root),
                                "--holdout",
                                str(self.runner.holdout_root),
                                "--results",
                                str(self.results_path),
                                "--progress",
                                str(self.progress_path),
                                "--stop-file",
                                str(self.stop_path),
                                "--instruments",
                                *instruments,
                            ],
                            stdin=subprocess.DEVNULL,
                            stdout=self._log_handle,
                            stderr=subprocess.STDOUT,
                            text=True,
                        )
                    except Exception:
                        self._log_handle.close()
                        self._log_handle = None
                        raise
                else:
                    self._thread = threading.Thread(
                        target=self._run, args=(instruments,), daemon=True, name="strategy-lab-test"
                    )
                    self._thread.start()
            return "Strategy Lab run started."
        if action == "status":
            if self._process:
                if self._process.poll() is None:
                    return self._progress_summary()
                self._process = None
                if self._log_handle is not None:
                    self._log_handle.close()
                    self._log_handle = None
                return self._summary()
            return (
                "Strategy Lab: run in progress."
                if self._thread and self._thread.is_alive()
                else ("Strategy Lab: ready." if self._result is None else self._summary())
            )
        if action == "results":
            return self._summary()
        if action == "stop":
            self._stop.set()
            if self._process and self._process.poll() is None:
                self.stop_path.parent.mkdir(parents=True, exist_ok=True)
                self.stop_path.write_text("stop\n", encoding="utf-8")
            return "Strategy Lab stop requested."
        if action == "chart":
            return "Strategy Lab chart is available only for a completed survivor."
        return (
            "Usage: /lab run [gold|nas100|all], /lab status, /lab results, "
            "/lab chart <id>, /lab stop"
        )

    def _run(self, instruments: Sequence[str]) -> None:
        try:
            self._result = self.runner.run(instruments=instruments, stop_event=self._stop)
        except Exception as exc:  # noqa: BLE001
            self._error = type(exc).__name__
            LOGGER.warning("strategy lab run failed (%s)", type(exc).__name__)

    def _progress_summary(self) -> str:
        try:
            payload = json.loads(self.progress_path.read_text(encoding="utf-8"))
            status = str(payload.get("status", "running"))[:20]
            done = int(payload.get("ideas_done", 0))
            total = int(payload.get("ideas_total", 0))
            survivors = int(payload.get("survivors", 0))
            return f"Strategy Lab: {status} ({done}/{total} ideas, {survivors} survivors)."
        except (OSError, ValueError, TypeError):
            return "Strategy Lab: run in progress."

    def _summary(self) -> str:
        if self._error:
            return "Strategy Lab: run failed safely."
        result = self._result
        if result is None and self.results_path.is_file():
            try:
                result = _result_from_payload(
                    json.loads(self.results_path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, TypeError, KeyError):
                result = None
        if result is None:
            return "Strategy Lab: no completed run."
        return LabRunner.format_results(result)

    def chart(self, _command: str, argument: str = "") -> bytes:
        """Render a bounded survivor comparison chart, including after restart."""
        result = self._result
        if result is None and self.results_path.is_file():
            try:
                result = _result_from_payload(
                    json.loads(self.results_path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise RuntimeError("no Strategy Lab survivor chart is available") from exc
        if result is None or not result.survivors:
            raise RuntimeError("no Strategy Lab survivor chart is available")
        requested = argument.split(maxsplit=1)[1] if len(argument.split()) > 1 else ""
        rows = [
            item
            for item in result.survivors
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
    "holdout_months",
    "load_histdata_candles",
    "prepare_holdout",
    "pessimistic_cost_model",
    "tune_variants",
]
