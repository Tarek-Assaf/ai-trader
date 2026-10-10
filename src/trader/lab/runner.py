"""Strategy Lab job runner and the read-only Telegram job facade.

The runner owns orchestration only.  It reads HistData Parquet, delegates
backtests and Gate 1 to the existing lab modules, and writes append-only
attempt records/results under the workspace.  It never imports broker or order
services and never promotes a strategy.
"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import io
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from array import array
from collections.abc import Iterator, Mapping, Sequence
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
from trader.lab.seeds import (
    instrument_seed_specs,
    level_seed_specs,
    seed_specs,
    slow_seed_specs,
    swing_seed_specs,
)
from trader.lab.spec import StrategySpec
from trader.lab.validator import open_sealed_reader, strategy_hash
from trader.viz import strategy_comparison

LOGGER = logging.getLogger(__name__)
INSTRUMENTS = ("NSXUSD", "XAUUSD")
TIMEFRAMES = (1, 3, 5, 15, 60)
Timeframe = Literal[1, 3, 5, 15, 60]
PROGRESS_HEARTBEAT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class ColumnarSeries:
    """Read-only columnar bars with lazy Candle compatibility.

    Numeric columns are compact arrays and are marked read-only before they
    cross a worker boundary.  Existing indicator code can still iterate the
    series, but the loader itself does not retain one Python object per bar.
    """

    timestamps: tuple[datetime, ...]
    opens: Any
    highs: Any
    lows: Any
    closes: Any
    volumes: Any
    instrument: str
    timeframe: int = 1

    @property
    def open(self) -> Any:
        return self.opens

    @property
    def high(self) -> Any:
        return self.highs

    @property
    def low(self) -> Any:
        return self.lows

    @property
    def close(self) -> Any:
        return self.closes

    @property
    def volume(self) -> Any:
        return self.volumes

    @property
    def ts_utc(self) -> tuple[datetime, ...]:
        return self.timestamps

    def __len__(self) -> int:
        return len(self.timestamps)

    def __iter__(self) -> Iterator[Candle]:
        return iter(self.to_candles())

    def __getitem__(self, index: int | slice) -> Candle | tuple[Candle, ...]:
        if isinstance(index, slice):
            return self.to_candles()[index]
        return self.to_candles()[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ColumnarSeries):
            return self.to_candles() == other.to_candles()
        if isinstance(other, tuple | list):
            return self.to_candles() == tuple(other)
        return NotImplemented

    def to_candles(self) -> tuple[Candle, ...]:
        return tuple(
            Candle(
                ts_utc=stamp,
                open=float(opening),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume),
                series_key=(self.instrument, "histdata"),
                n_minutes=self.timeframe,
            )
            for stamp, opening, high, low, close, volume in zip(
                self.timestamps,
                self.opens,
                self.highs,
                self.lows,
                self.closes,
                self.volumes,
                strict=True,
            )
        )


def _readonly_numeric(values: Sequence[float]) -> Any:
    """Return a compact immutable numeric array without requiring NumPy."""
    try:
        import numpy as np

        result = np.asarray(values, dtype="float64")
        result.setflags(write=False)
        return result
    except ImportError:  # pragma: no cover - pyarrow environments normally have NumPy
        return array("d", (float(value) for value in values))


def _columnar_series(
    timestamps: Sequence[datetime],
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    instrument: str,
    timeframe: int,
) -> ColumnarSeries:
    return ColumnarSeries(
        tuple(timestamps),
        _readonly_numeric(opens),
        _readonly_numeric(highs),
        _readonly_numeric(lows),
        _readonly_numeric(closes),
        _readonly_numeric(volumes),
        instrument,
        timeframe,
    )


def walk_forward_sizes(bars: int) -> tuple[int, int, int]:
    """Return (train, test, step) bar counts that give eight walk-forward windows.

    The previous sizes (half the frame to train, a fifth to test, a fifth to step)
    can only fit two windows, but Gate 1 requires at least five, so no strategy
    could ever pass. A third of the frame to train with a twelfth to test and
    step fits eight windows (starts at 0, 1/12, ... 7/12 of the frame).
    """
    return max(1, bars // 3), max(1, bars // 12), max(1, bars // 12)


def read_memory_limit(path: str | Path = "/sys/fs/cgroup/memory.max") -> int | None:
    """Read the cgroup memory limit; ``None`` means unlimited/unavailable."""
    try:
        value = Path(path).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if not value or value == "max":
        return None
    try:
        limit = int(value)
    except ValueError:
        return None
    return limit if limit > 0 else None


def worker_cap(
    *,
    cpu_count: int | None = None,
    memory_limit_bytes: int | None = None,
    per_worker_bytes: int = 700 * 1024**2,
    memory_fraction: float = 0.35,
    max_workers: int = 4,
) -> int:
    """Bound workers to CPU-2, 35% of the cgroup memory budget and ``max_workers``.

    The Pod also runs the recorder, API, Redis and Postgres, so the lab may not
    take half the memory: a seeds-only run with 10 workers reached 9.2 GB.

    ``per_worker_bytes`` is intentionally conservative: it is a sizing limit,
    not a measurement or a promise that a worker may exceed the cgroup cap.
    """
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    cpu_limit = max(1, min(cpus - 2, max_workers))
    if memory_limit_bytes is None:
        return cpu_limit
    if per_worker_bytes <= 0 or not 0 < memory_fraction <= 1:
        raise ValueError("worker memory parameters must be positive")
    memory_limit = int(memory_limit_bytes * memory_fraction // per_worker_bytes)
    return max(1, min(cpu_limit, memory_limit))


def cap_worker_count(requested: int | None = None, **kwargs: Any) -> int:
    """Compatibility wrapper used by scripts and tests."""
    cap = worker_cap(**kwargs)
    return max(1, min(cap, requested)) if requested is not None else cap


def process_rss_bytes() -> int:
    """Return current process RSS where the host exposes it, else zero."""
    try:
        import resource

        getrusage = getattr(resource, "getrusage", None)
        if getrusage is None:
            return 0
        usage = getrusage(getattr(resource, "RUSAGE_SELF", 0)).ru_maxrss
        return int(usage * (1024 if sys.platform != "darwin" else 1))
    except (ImportError, AttributeError, OSError):
        return 0


def _proc_stat(pid: int) -> tuple[int, int] | None:
    """Return (parent pid, resident bytes) from Linux /proc, if available."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(fields[1]), int(fields[21]) * page_size
    except (OSError, ValueError, IndexError, TypeError):
        return None


def _proc_pss(pid: int) -> int:
    """Return proportional set size for a process, falling back to zero."""
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text(encoding="ascii").splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _descendant_pids(root_pid: int) -> tuple[int, ...]:
    children: dict[int, list[int]] = {}
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        stat = _proc_stat(pid)
        if stat is not None:
            children.setdefault(stat[0], []).append(pid)
    result: list[int] = []
    pending = list(children.get(root_pid, ()))
    while pending:
        pid = pending.pop()
        result.append(pid)
        pending.extend(children.get(pid, ()))
    return tuple(result)


def _cgroup_memory_stat_anon() -> int:
    try:
        for line in Path("/sys/fs/cgroup/memory.stat").read_text(encoding="ascii").splitlines():
            if line.startswith("anon "):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _cgroup_memory_current() -> int:
    try:
        return int(Path("/sys/fs/cgroup/memory.current").read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0


def process_memory_diagnostics() -> dict[str, int]:
    """Capture main RSS, descendant worker memory and cgroup memory counters."""
    pid = os.getpid()
    descendants = _descendant_pids(pid)
    main_rss = (_proc_stat(pid) or (0, 0))[1] or process_rss_bytes()
    worker_pss = sum(_proc_pss(child) for child in descendants)
    if worker_pss == 0:
        worker_pss = sum((_proc_stat(child) or (0, 0))[1] for child in descendants)
    return {
        "rss_bytes": int(main_rss),
        "worker_pss_bytes": int(worker_pss),
        "cgroup_anon_bytes": _cgroup_memory_stat_anon(),
        "memory_current_bytes": _cgroup_memory_current(),
    }


class RunFileLock:
    """Small cross-process lock implemented with atomic file creation."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor: int | None = None
        for _ in range(3):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError as exc:
                stale = False
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                    pid = int(payload["pid"])
                except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
                    stale = True
                    pid = None
                    LOGGER.warning("reclaiming malformed Strategy Lab lock %s", self.path)
                else:
                    assert pid is not None
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        stale = True
                        LOGGER.warning(
                            "reclaiming dead Strategy Lab lock pid=%s path=%s", pid, self.path
                        )
                    except PermissionError:
                        # The process exists but belongs to another user; fail safe.
                        pass
                if not stale:
                    raise RuntimeError("a Strategy Lab run is already in progress") from exc
                quarantine = self.path.with_name(
                    f".{self.path.name}.stale.{os.getpid()}.{uuid.uuid4().hex}"
                )
                try:
                    os.rename(self.path, quarantine)
                except FileNotFoundError:
                    continue
                else:
                    quarantine.unlink(missing_ok=True)
        if descriptor is None:
            raise RuntimeError("could not reclaim the Strategy Lab lock safely")
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat()})
            )
        self._owned = True

    def release(self) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False

    def __enter__(self) -> RunFileLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


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


def load_histdata_columns(
    root: str | Path,
    instrument: str,
    timeframe: int = 1,
    *,
    exclude_months: set[tuple[int, int]] | None = None,
    cache_root: str | Path | None = None,
) -> ColumnarSeries:
    """Load HistData into compact read-only columns.

    Parquet columns are materialized in bulk.  The compatibility Candle view
    is created only when downstream indicator code explicitly iterates it.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError("timeframe must be one of 1, 3, 5, 15 or 60 minutes")
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pyarrow is required for Strategy Lab data") from exc
    files = _parquet_files(Path(root), instrument)
    excluded = exclude_months or set()
    cache_path = _cache_path(files, excluded, instrument, timeframe, cache_root)
    if cache_path is not None and cache_path.is_file():
        try:
            return _read_columnar_cache(cache_path, instrument, timeframe=timeframe)
        except (OSError, KeyError, TypeError, ValueError, OverflowError):
            cache_path.unlink(missing_ok=True)

    timestamps: list[datetime] = []
    columns: dict[str, list[float]] = {
        name: [] for name in ("open", "high", "low", "close", "volume")
    }
    for path in files:
        try:
            available = set(pq.ParquetFile(path).schema.names)
            required = ["ts_utc", "open", "high", "low", "close"]
            if not set(required).issubset(available):
                continue
            names = required + (["volume"] if "volume" in available else [])
            table = pq.read_table(path, columns=names)
            stamps = [_as_utc(value) for value in table["ts_utc"].to_pylist()]
            values = {
                name: table[name].to_pylist() if name in table.column_names else [0.0] * len(stamps)
                for name in ("open", "high", "low", "close", "volume")
            }
            for index, stamp in enumerate(stamps):
                if (stamp.year, stamp.month) in excluded:
                    continue
                numeric = tuple(float(values[name][index] or 0.0) for name in columns)
                if not all(math.isfinite(value) for value in numeric[:4]):
                    continue
                timestamps.append(stamp)
                for name, value in zip(columns, numeric, strict=True):
                    columns[name].append(value)
        except (OSError, KeyError, ValueError, TypeError, OverflowError):
            continue

    order = sorted(range(len(timestamps)), key=timestamps.__getitem__)
    unique_indices: list[int] = []
    previous: datetime | None = None
    for index in order:
        stamp = timestamps[index]
        if stamp != previous:
            unique_indices.append(index)
            previous = stamp
    series = _columnar_series(
        [timestamps[index] for index in unique_indices],
        [columns["open"][index] for index in unique_indices],
        [columns["high"][index] for index in unique_indices],
        [columns["low"][index] for index in unique_indices],
        [columns["close"][index] for index in unique_indices],
        [columns["volume"][index] for index in unique_indices],
        instrument,
        1,
    )
    if timeframe != 1:
        resampled = tuple(
            item for item in resample_ohlc(series.to_candles(), timeframe) if item.complete
        )
        series = _columnar_series(
            [item.ts_utc for item in resampled],
            [item.open for item in resampled],
            [item.high for item in resampled],
            [item.low for item in resampled],
            [item.close for item in resampled],
            [item.volume for item in resampled],
            instrument,
            timeframe,
        )
    if cache_path is not None:
        try:
            _write_candle_cache(cache_path, series)
        except (OSError, ValueError, TypeError):
            LOGGER.warning("lab series cache write failed (%s)", type(cache_path).__name__)
    return series


def load_histdata_arrays(
    root: str | Path,
    instrument: str,
    timeframe: int = 1,
    *,
    exclude_months: set[tuple[int, int]] | None = None,
    cache_root: str | Path | None = None,
) -> ColumnarSeries:
    """Alias with an explicit name for callers that require array output."""
    return load_histdata_columns(
        root,
        instrument,
        timeframe,
        exclude_months=exclude_months,
        cache_root=cache_root,
    )


def load_histdata_candles(
    root: str | Path,
    instrument: str,
    timeframe: int = 1,
    *,
    exclude_months: set[tuple[int, int]] | None = None,
    cache_root: str | Path | None = None,
) -> tuple[Candle, ...]:
    """Read HistData as the backwards-compatible lazy Candle view."""
    return load_histdata_columns(
        root,
        instrument,
        timeframe,
        exclude_months=exclude_months,
        cache_root=cache_root,
    ).to_candles()


def _cache_path(
    files: Sequence[Path],
    excluded: set[tuple[int, int]],
    instrument: str,
    timeframe: int,
    cache_root: str | Path | None,
) -> Path | None:
    if cache_root is None:
        return None
    signature = {
        "files": [
            {
                "path": str(path),
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            }
            for path in files
        ],
        "exclude_months": sorted(f"{year:04d}-{month:02d}" for year, month in excluded),
        "instrument": instrument,
        "timeframe": timeframe,
    }
    digest = hashlib.sha256(
        json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return Path(cache_root) / f"{instrument.lower()}-{timeframe}m-{digest}.parquet"


def _read_candle_columns(path: Path, instrument: str, *, timeframe: int) -> tuple[Candle, ...]:
    return _read_columnar_cache(path, instrument, timeframe=timeframe).to_candles()


def _read_columnar_cache(path: Path, instrument: str, *, timeframe: int) -> ColumnarSeries:
    import pyarrow.parquet as pq

    table = pq.read_table(path, columns=["ts_utc", "open", "high", "low", "close", "volume"])
    columns = {name: table[name].to_pylist() for name in table.column_names}
    return _columnar_series(
        [_as_utc(stamp) for stamp in columns["ts_utc"]],
        [float(value) for value in columns["open"]],
        [float(value) for value in columns["high"]],
        [float(value) for value in columns["low"]],
        [float(value) for value in columns["close"]],
        [float(value or 0.0) for value in columns.get("volume", [])],
        instrument,
        timeframe,
    )


def _write_candle_cache(path: Path, candles: Sequence[Candle] | ColumnarSeries) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    series = (
        candles
        if isinstance(candles, ColumnarSeries)
        else _columnar_series(
            [item.ts_utc for item in candles],
            [item.open for item in candles],
            [item.high for item in candles],
            [item.low for item in candles],
            [item.close for item in candles],
            [item.volume for item in candles],
            "cache",
            1,
        )
    )
    table = pa.table(
        {
            "ts_utc": list(series.timestamps),
            "open": series.opens,
            "high": series.highs,
            "low": series.lows,
            "close": series.closes,
            "volume": series.volumes,
        }
    )
    temporary = path.with_suffix(".tmp.parquet")
    pq.write_table(table, temporary)
    temporary.replace(path)


def coverage_report(root: str | Path, instrument: str) -> Coverage:
    """Summarize lake coverage and QA sidecars without opening broker state."""
    import pyarrow.parquet as pq

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
    first: datetime | None = None
    last: datetime | None = None
    row_count = 0
    for path in files:
        try:
            metadata = pq.ParquetFile(path).metadata
            row_count += metadata.num_rows
            for index in range(metadata.num_row_groups):
                group = metadata.row_group(index)
                for column_index in range(group.num_columns):
                    column = group.column(column_index)
                    if column.path_in_schema != "ts_utc" or column.statistics is None:
                        continue
                    lower = _as_utc(column.statistics.min)
                    upper = _as_utc(column.statistics.max)
                    first = lower if first is None else min(first, lower)
                    last = upper if last is None else max(last, upper)
        except (OSError, KeyError, TypeError, ValueError, OverflowError):
            continue
    if row_count and (first is None or last is None):
        timestamps = load_histdata_candles(root, instrument)
        first = timestamps[0].ts_utc if timestamps else None
        last = timestamps[-1].ts_utc if timestamps else None
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


def strategy_lab_holdout_months(config_path: str | Path = "config/strategy_lab.yaml") -> int:
    """Read the sealed-holdout window from the Strategy Lab configuration.

    The scripts deliberately do not carry a second default for this safety
    setting.  Keeping the value in ``strategy_lab.yaml`` means changing the
    holdout policy is an explicit configuration change that is visible in
    review and in the run evidence.
    """
    import yaml

    try:
        payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        value = payload["data"]["holdout_months"]
    except (KeyError, TypeError, OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError("strategy_lab.yaml data.holdout_months is invalid") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("strategy_lab.yaml data.holdout_months must be a positive integer")
    return cast(int, value)


def strategy_lab_max_training_years(
    config_path: str | Path = "config/strategy_lab.yaml",
) -> int | None:
    """Read the bounded training window; ``all`` disables the bound."""
    import yaml

    try:
        payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        value = payload["data"]["max_training_years"]
    except (KeyError, TypeError, OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError("strategy_lab.yaml data.max_training_years is invalid") from exc
    if value == "all":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            "strategy_lab.yaml data.max_training_years must be a positive integer or all"
        )
    return cast(int, value)


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
        cache_root: str | Path = "/workspace/state/lab/cache",
        max_training_years: int | None = 6,
        lock_path: str | Path | None = None,
    ) -> None:
        self.lake_root = Path(lake_root)
        self.holdout_root = Path(holdout_root).resolve()
        self.ledger = ledger or ExperimentLedger()
        self.config_path = Path(config_path)
        self.idea_generator = idea_generator
        self.cache_root = Path(cache_root)
        self.max_training_years = max_training_years
        self.lock_path = Path(lock_path) if lock_path is not None else None
        self._heartbeat_stop: threading.Event | None = None
        self._heartbeat_thread: threading.Thread | None = None

    def _candidates(
        self,
        instrument: str,
        timeframe: int,
        sources: Sequence[str],
        candles: Sequence[Candle],
        *,
        workers: int | None = None,
        progress_callback: Any | None = None,
    ) -> list[tuple[str, Any]]:
        result: list[tuple[str, Any]] = []
        if "seeds" in sources or "all" in sources:
            for seed, spec in (
                *seed_specs(instrument, timeframe),
                *slow_seed_specs(instrument, timeframe),
                *swing_seed_specs(instrument, timeframe),
                *level_seed_specs(instrument, timeframe),
                *instrument_seed_specs(instrument, timeframe),
            ):
                result.extend((f"seed:{seed.name}", variant) for variant in tune_variants(spec))
        if ("patterns" in sources or "pattern" in sources or "all" in sources) and candles:
            mined = PatternMiner(
                instrument=instrument,
                timeframe_minutes=cast(Timeframe, timeframe),
                ledger=self.ledger,
                workers=workers,
                progress_callback=progress_callback,
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

    def run(self, *args: Any, **kwargs: Any) -> LabRunResult:
        """Run a job while refusing concurrent processes sharing a lock file."""
        lock_path = self.lock_path
        progress_path = kwargs.get("progress_path")
        if lock_path is None and progress_path is not None:
            lock_path = Path(progress_path).parent / "run.lock"
        if lock_path is None:
            return self._run_unlocked(*args, **kwargs)
        with RunFileLock(lock_path):
            return self._run_unlocked(*args, **kwargs)

    def _run_unlocked(self, *args: Any, **kwargs: Any) -> LabRunResult:
        try:
            return self._run_unlocked_impl(*args, **kwargs)
        except Exception as exc:
            heartbeat_stop = self._heartbeat_stop
            heartbeat_thread = self._heartbeat_thread
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=2.0)
            progress_path = kwargs.get("progress_path")
            if progress_path is not None:
                target = Path(progress_path)
                try:
                    payload = json.loads(target.read_text(encoding="utf-8"))
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    payload = {"pid": os.getpid()}
                payload.update(
                    {
                        "status": "failed",
                        "error": type(exc).__name__,
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )
                temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            raise
        finally:
            self._heartbeat_stop = None
            self._heartbeat_thread = None

    def _run_unlocked_impl(
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
        memory_limit = read_memory_limit()
        workers = cap_worker_count(
            workers,
            cpu_count=os.cpu_count(),
            memory_limit_bytes=memory_limit,
        )
        peak_rss = process_rss_bytes()
        attempts: list[LabAttempt] = []
        completed_ideas = 0
        total_ideas = 0
        phase_started = time.monotonic()
        phase_timings: dict[str, float] = {}
        phase_memory: dict[str, dict[str, int]] = {}
        current_phase = "loading"
        progress_lock = threading.RLock()
        heartbeat_stop = threading.Event()
        heartbeat_thread: threading.Thread | None = None

        def write_progress(status: str = "running") -> None:
            nonlocal peak_rss
            if progress_path is None:
                return
            with progress_lock:
                peak_rss = max(peak_rss, process_rss_bytes())
                diagnostics = process_memory_diagnostics()
                previous = phase_memory.get(current_phase, {})
                phase_memory[current_phase] = {
                    key: max(value, previous.get(key, 0)) for key, value in diagnostics.items()
                }
                target = Path(progress_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                now = datetime.now(UTC).isoformat()
                payload = json.dumps(
                    {
                        "status": status,
                        "run_id": run_name,
                        "pid": os.getpid(),
                        "started_at": started.isoformat(),
                        "updated_at": now,
                        "ideas_done": completed_ideas,
                        "ideas_total": total_ideas,
                        "survivors": sum(item.verdict == "pass" for item in attempts),
                        "phase": current_phase,
                        "phase_timings_seconds": dict(phase_timings),
                        "peak_rss_bytes": peak_rss,
                        "memory_limit_bytes": memory_limit,
                        "workers": workers,
                        "rss_bytes": diagnostics["rss_bytes"],
                        "worker_pss_bytes": diagnostics["worker_pss_bytes"],
                        "cgroup_anon_bytes": diagnostics["cgroup_anon_bytes"],
                        "memory_current_bytes": diagnostics["memory_current_bytes"],
                        "memory_diagnostics": dict(phase_memory),
                    },
                    sort_keys=True,
                )
                temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
                try:
                    temporary.write_text(payload, encoding="utf-8")
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)

        def progress_heartbeat() -> None:
            while not heartbeat_stop.wait(PROGRESS_HEARTBEAT_SECONDS):
                write_progress()

        write_progress()

        def mark_phase(name: str) -> None:
            nonlocal current_phase, phase_started
            now = time.monotonic()
            phase_timings[current_phase] = phase_timings.get(current_phase, 0.0) + (
                now - phase_started
            )
            current_phase = name
            phase_started = now
            print(f"[{datetime.now(UTC).isoformat()}] {name}", flush=True)
            write_progress()

        coverage = tuple(coverage_report(self.lake_root, instrument) for instrument in instruments)
        holdout_files = tuple(self.holdout_root.glob("**/*.parquet"))
        if not holdout_files:
            raise RuntimeError(
                "Strategy Lab holdout is absent; run lab_data_check.py --prepare-holdout first"
            )
        sealed_months = holdout_months(self.holdout_root, instruments)
        if progress_path is not None:
            heartbeat_thread = threading.Thread(
                target=progress_heartbeat,
                name="lab-progress-heartbeat",
                daemon=True,
            )
            self._heartbeat_stop = heartbeat_stop
            self._heartbeat_thread = heartbeat_thread
            heartbeat_thread.start()

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
                list[tuple[str, Any]],
                list[StrategySpec],
                Path,
            ]
        ] = []
        for instrument, timeframe in ((i, t) for i in instruments for t in timeframes):
            mark_phase(f"loading:{instrument}:{timeframe}m")
            candles = load_histdata_candles(
                self.lake_root,
                instrument,
                timeframe,
                exclude_months=sealed_months,
                cache_root=self.cache_root,
            )
            if self.max_training_years is not None and sealed_months:
                first_holdout = min(sealed_months)
                cutoff = datetime(
                    first_holdout[0] - self.max_training_years,
                    first_holdout[1],
                    1,
                    tzinfo=UTC,
                )
                candles = tuple(item for item in candles if item.ts_utc >= cutoff)
            if len(candles) < 10:
                continue
            split = max(1, int(len(candles) * 0.8))
            train, test = candles[:split], candles[split:]
            if not test:
                continue
            mark_phase(f"mining:{instrument}:{timeframe}m:0/20")

            # Idea mining and parameter tuning must never inspect the later
            # evaluation window. The sealed holdout has already been excluded
            # from ``candles`` above, so this is the remaining in-sample slice.
            def mining_progress(
                done: int,
                total: int,
                instrument: str = instrument,
                timeframe: int = timeframe,
            ) -> None:
                mark_phase(f"mining:{instrument}:{timeframe}m:{done}/{total}")

            try:
                grouped = self._candidates(
                    instrument,
                    timeframe,
                    idea_sources,
                    train,
                    workers=workers,
                    progress_callback=mining_progress,
                )
            except TypeError as exc:
                # Keep test and plugin subclasses written against the T-114
                # hook signature working while they migrate to T-116.
                if "unexpected keyword argument" not in str(exc):
                    raise
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
                # Keep only specs and paths between phases.  The candle
                # arrays are reopened for each evaluation group below so a
                # completed instrument can be reclaimed before the next one.
                (instrument, timeframe, candidates, specs, holdout)
            )

        existing_hashes = {
            item.strategy_hash
            for item in self.ledger.records()
            if item.parameters.get("pipeline")
            not in {"neighbor", "gate1_screen", "gate1_final", "worker"}
        }
        current_hashes = {
            strategy_hash(spec.model_dump(mode="json"))
            for _, _, candidates, _, _ in groups
            for _, spec in candidates
        }
        trial_count = max(1, len(existing_hashes | current_hashes))
        total_ideas = sum(len(group[2]) for group in groups)
        write_progress()
        for instrument, timeframe, candidates, specs, holdout in groups:
            if stopped():
                break
            # Reopen only the current group. This keeps the parent from
            # retaining all instruments while worker results are evaluated.
            mark_phase(f"loading:{instrument}:{timeframe}m:reopen")
            candles = load_histdata_candles(
                self.lake_root,
                instrument,
                timeframe,
                exclude_months=sealed_months,
                cache_root=self.cache_root,
            )
            if self.max_training_years is not None and sealed_months:
                first_holdout = min(sealed_months)
                cutoff = datetime(
                    first_holdout[0] - self.max_training_years,
                    first_holdout[1],
                    1,
                    tzinfo=UTC,
                )
                candles = tuple(item for item in candles if item.ts_utc >= cutoff)
            split = max(1, int(len(candles) * 0.8))
            train, test = candles[:split], candles[split:]
            if not test:
                del candles, train, test
                gc.collect()
                continue
            reader = open_sealed_reader(self.holdout_root, self.ledger)
            pipeline = ValidationPipeline(
                ledger=self.ledger, cost_model=pessimistic_cost_model(self.config_path)
            )
            mark_phase(f"evaluating:0/{len(candidates)}:{instrument}:{timeframe}m")
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
                    train_bars=walk_forward_sizes(len(train))[0],
                    test_bars=walk_forward_sizes(len(train))[1],
                    step_bars=walk_forward_sizes(len(train))[2],
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
                mark_phase(
                    f"evaluating:{len(candidates)}/{len(candidates)}:{instrument}:{timeframe}m"
                )
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
            mark_phase(f"evaluating:{len(candidates)}/{len(candidates)}:{instrument}:{timeframe}m")
            mark_phase(f"holdout:{instrument}:{timeframe}m")
            write_progress()
            del candles, train, test
            gc.collect()
        finished = datetime.now(UTC)
        mark_phase("done")
        phase_timings.setdefault("done", 0.0)
        survivors = tuple(item for item in attempts if item.verdict == "pass")
        write_progress("completed")
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2.0)
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
                                "--lock-file",
                                str(self.state_root / "run.lock"),
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
    "ColumnarSeries",
    "Coverage",
    "LabAttempt",
    "LabJobManager",
    "LabRunResult",
    "LabRunner",
    "coverage_report",
    "cap_worker_count",
    "walk_forward_sizes",
    "holdout_months",
    "load_histdata_arrays",
    "load_histdata_candles",
    "load_histdata_columns",
    "prepare_holdout",
    "pessimistic_cost_model",
    "read_memory_limit",
    "RunFileLock",
    "strategy_lab_holdout_months",
    "strategy_lab_max_training_years",
    "tune_variants",
]
