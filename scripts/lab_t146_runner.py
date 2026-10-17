"""Run the T-146 real-lake study one bounded symbol/timeframe group at a time.

The matching script remains the single source of truth for study calculations.  This
wrapper only provides resumable execution, checkpoints, and a wall-clock guard around
one child process.  It never opens a broker connection or submits an order.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

GROUP_ORDER: tuple[tuple[str, int], ...] = (
    ("NSXUSD", 60),
    ("XAUUSD", 60),
    ("NSXUSD", 5),
    ("XAUUSD", 5),
    ("NSXUSD", 3),
    ("XAUUSD", 3),
    ("NSXUSD", 1),
    ("XAUUSD", 1),
)
MATCHING_SCRIPT = Path(__file__).with_name("lab_t146_matching.py")
DEFAULT_TIME_LIMIT_SECONDS = 20 * 60
DEFAULT_MAX_QUERIES = 2
POLL_SECONDS = 1.0


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    os.replace(temporary, path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _group_key(instrument: str, timeframe: int) -> str:
    return f"{instrument}/{timeframe}"


def _empty_progress() -> dict[str, Any]:
    return {
        "version": 1,
        "groups": {
            _group_key(instrument, timeframe): {
                "instrument": instrument,
                "timeframe": timeframe,
                "status": "pending",
                "queries_done": 0,
                "elapsed_seconds": 0.0,
            }
            for instrument, timeframe in GROUP_ORDER
        },
    }


def load_progress(path: Path) -> dict[str, Any]:
    progress = _read_json(path, None)
    if not isinstance(progress, dict) or not isinstance(progress.get("groups"), dict):
        progress = _empty_progress()
    for instrument, timeframe in GROUP_ORDER:
        key = _group_key(instrument, timeframe)
        entry = progress["groups"].setdefault(key, {})
        entry.setdefault("instrument", instrument)
        entry.setdefault("timeframe", timeframe)
        entry.setdefault("status", "pending")
        entry.setdefault("queries_done", 0)
        entry.setdefault("elapsed_seconds", 0.0)
    return progress


def next_group(progress: dict[str, Any]) -> tuple[str, int] | None:
    """Return the first group not yet completed or marked partial."""
    groups = progress.get("groups", {})
    for instrument, timeframe in GROUP_ORDER:
        status = groups.get(_group_key(instrument, timeframe), {}).get("status", "pending")
        if status not in {"complete", "partial"}:
            return instrument, timeframe
    return None


def _rss_bytes(pid: int) -> int | None:
    """Read a Linux child's resident set size without requiring broker access."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (FileNotFoundError, OSError, ValueError):
        return None
    return None


def _child_command(
    *,
    lake: Path,
    group_output: Path,
    group_progress: Path,
    holdout_end: str,
    instrument: str,
    timeframe: int,
    max_queries: int,
    seed: int,
) -> list[str]:
    return [
        sys.executable,
        str(MATCHING_SCRIPT),
        "--lake",
        str(lake),
        "--output",
        str(group_output),
        "--holdout-end",
        holdout_end,
        "--instrument",
        instrument,
        "--timeframe",
        str(timeframe),
        "--progress-file",
        str(group_progress),
        "--max-queries",
        str(max_queries),
        "--seed",
        str(seed),
    ]


def _read_child_progress(path: Path, instrument: str, timeframe: int) -> dict[str, Any]:
    child = _read_json(path, {})
    if not isinstance(child, dict):
        child = {}
    return {
        "instrument": instrument,
        "timeframe": timeframe,
        "queries_done": int(child.get("queries_done", 0) or 0),
        "elapsed_seconds": float(child.get("elapsed_seconds", 0.0) or 0.0),
    }


def run_group_with_timeout(
    *,
    instrument: str,
    timeframe: int,
    lake: Path,
    output: Path,
    holdout_end: str,
    max_queries: int = DEFAULT_MAX_QUERIES,
    time_limit_seconds: float = DEFAULT_TIME_LIMIT_SECONDS,
    seed: int = 146,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    popen_factory: Callable[..., Any] = subprocess.Popen,
) -> dict[str, Any]:
    """Run one child group and mark it partial when the wall-clock limit expires."""
    key = _group_key(instrument, timeframe)
    group_output = output / "groups" / f"{instrument.lower()}_{timeframe}m"
    group_progress = group_output / "progress.json"
    group_output.mkdir(parents=True, exist_ok=True)
    command = _child_command(
        lake=lake,
        group_output=group_output,
        group_progress=group_progress,
        holdout_end=holdout_end,
        instrument=instrument,
        timeframe=timeframe,
        max_queries=max_queries,
        seed=seed,
    )
    started = clock()
    # Child output goes to a log file: an undrained PIPE can fill up and hang the child.
    with (group_output / "child.log").open("w", encoding="utf-8") as log_handle:
        process = popen_factory(command, stdout=log_handle, stderr=subprocess.STDOUT, text=True)
        return _supervise(
            process,
            key=key,
            instrument=instrument,
            timeframe=timeframe,
            group_output=group_output,
            group_progress=group_progress,
            started=started,
            time_limit_seconds=time_limit_seconds,
            clock=clock,
            sleep=sleep,
        )


def _supervise(
    process: Any,
    *,
    key: str,
    instrument: str,
    timeframe: int,
    group_output: Path,
    group_progress: Path,
    started: float,
    time_limit_seconds: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    peak_rss = 0
    timed_out = False
    while process.poll() is None:
        rss = _rss_bytes(getattr(process, "pid", -1))
        if rss is not None:
            peak_rss = max(peak_rss, rss)
        elapsed = clock() - started
        if elapsed >= time_limit_seconds:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            break
        sleep(min(POLL_SECONDS, max(0.0, time_limit_seconds - elapsed)))
    if process.poll() is None:
        process.wait()
    elapsed = max(0.0, clock() - started)
    child = _read_child_progress(group_progress, instrument, timeframe)
    evidence_path = group_output / f"t146_group_{instrument.lower()}_{timeframe}m.json"
    status = "partial" if timed_out else ("complete" if process.returncode == 0 else "failed")
    record: dict[str, Any] = {
        "key": key,
        "instrument": instrument,
        "timeframe": timeframe,
        "status": status,
        "queries_done": child["queries_done"],
        "elapsed_seconds": round(elapsed, 3),
        "exit_code": process.returncode,
        "rss_peak_bytes": peak_rss or None,
        "output_dir": str(group_output),
    }
    if status == "complete" and evidence_path.exists():
        record["evidence_file"] = str(evidence_path)
        record["evidence"] = _read_json(evidence_path, {})
    return record


def _aggregate_record(record: dict[str, Any]) -> dict[str, Any]:
    evidence = record.get("evidence")
    if evidence is None and record.get("evidence_file"):
        evidence = _read_json(Path(record["evidence_file"]), None)
    if isinstance(evidence, dict):
        return evidence
    return {
        "instrument": record["instrument"],
        "timeframe": record["timeframe"],
        "library_windows": 0,
        "oos_queries": record.get("queries_done", 0),
        "queries_evaluated": record.get("queries_done", 0),
        "results": [],
        "elapsed_seconds": record.get("elapsed_seconds", 0.0),
        "charts_dir": record.get("output_dir", ""),
        "query_stride": None,
        "status": record.get("status", "partial"),
    }


def write_checkpoint(output: Path, progress: dict[str, Any], *, holdout_end: str) -> None:
    groups = [
        _aggregate_record(entry)
        for entry in progress["groups"].values()
        if entry.get("status") != "pending"
    ]
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "study": "T-146 movement matching",
        "split": {"holdout_end": holdout_end},
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "paper_trading": False,
            "gate1_evaluated": False,
            "ledger_writes": 0,
            "promotion_writes": 0,
        },
        "groups": groups,
    }
    _write_json(output / "t146_matching_summary.json", payload)
    lines = [
        "# T-146 matching study checkpoint",
        "",
        "This report contains bounded real-lake group checkpoints. "
        "No broker calls or orders were made.",
        "",
        "## Groups",
        "",
    ]
    for group in groups:
        lines.append(
            f"- **{group['instrument']} {group['timeframe']}m**: "
            f"status={group.get('status', 'complete')}, "
            f"queries={group.get('queries_evaluated', 0)}, "
            f"elapsed={group.get('elapsed_seconds', 0)}s."
        )
    (output / "t146_matching_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_once(args: argparse.Namespace, *, clock: Callable[[], float] = time.monotonic) -> int:
    progress = load_progress(args.progress)
    group = (
        (args.instrument, args.timeframe)
        if args.instrument is not None and args.timeframe is not None
        else next_group(progress)
    )
    if group is None:
        write_checkpoint(args.output, progress, holdout_end=args.holdout_end)
        return 0
    instrument, timeframe = group
    key = _group_key(instrument, timeframe)
    progress["groups"][key].update({"status": "running", "started_at": time.time()})
    _write_json(args.progress, progress)
    record = run_group_with_timeout(
        instrument=instrument,
        timeframe=timeframe,
        lake=args.lake,
        output=args.output,
        holdout_end=args.holdout_end,
        max_queries=args.max_queries,
        time_limit_seconds=args.group_time_limit_seconds,
        seed=args.seed,
    )
    progress["groups"][key].update({k: v for k, v in record.items() if k != "evidence"})
    _write_json(args.progress, progress)
    write_checkpoint(args.output, progress, holdout_end=args.holdout_end)
    return 0 if record["status"] in {"complete", "partial"} else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--output", type=Path, default=Path("docs/evidence"))
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--holdout-end", default="2026-07-01T00:00:00+00:00")
    parser.add_argument("--instrument", choices=tuple(sorted({item[0] for item in GROUP_ORDER})))
    parser.add_argument(
        "--timeframe", type=int, choices=tuple(sorted({item[1] for item in GROUP_ORDER}))
    )
    parser.add_argument("--max-queries", type=int, default=DEFAULT_MAX_QUERIES)
    parser.add_argument(
        "--group-time-limit-seconds", type=float, default=DEFAULT_TIME_LIMIT_SECONDS
    )
    parser.add_argument("--seed", type=int, default=146)
    args = parser.parse_args(argv)
    if (args.instrument is None) != (args.timeframe is None):
        parser.error("--instrument and --timeframe must be provided together")
    args.progress = args.progress or args.output / "t153_progress.json"
    if args.max_queries < 1:
        parser.error("--max-queries must be positive")
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
