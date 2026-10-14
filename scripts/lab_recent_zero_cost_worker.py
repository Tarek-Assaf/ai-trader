#!/usr/bin/env python3
"""Run one disjoint T-143 candidate range with an isolated checkpoint.

This worker deliberately reuses the T-143 diagnostic helpers but writes only
its own checkpoint, progress, and output files.  It never reads the sealed
holdout or contacts the broker.  Multiple workers can therefore run beside
the original resumable T-143 process and be merged after all exit-code files
are present.
"""

from __future__ import annotations

import argparse
import gc
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lab_recent_zero_cost import (
    INSTRUMENTS,
    MIN_GROUP_TRADES,
    _candidate_specs,
    _evaluate,
    _group_summary,
    _load_window,
    _memory_bytes,
    _stamp,
    _write_json,
    _zero_cost,
    pessimistic_cost_model,
    spec_hash,
)


def run(args: argparse.Namespace) -> int:
    start = datetime.fromisoformat(args.start_date).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end_date).replace(tzinfo=UTC)
    if end <= start:
        raise ValueError("end date must be after start date")
    if args.candidate_start < 0 or args.candidate_end <= args.candidate_start:
        raise ValueError("candidate range must be non-empty and non-negative")

    group = f"{args.instrument}:{args.timeframe}"
    all_candidates = _candidate_specs(
        args.instrument,
        args.timeframe,
        staged_generations=args.staged_generations,
        max_staged_parents=args.max_staged_parents,
    )
    candidates = all_candidates[args.candidate_start : args.candidate_end]
    if not candidates:
        raise ValueError("candidate range is outside the generated candidate list")

    checkpoint_path = Path(args.checkpoint)
    checkpoint: dict[str, Any] = {"completed": {}, "started_at": _stamp()}
    if checkpoint_path.exists():
        checkpoint.update(json.loads(checkpoint_path.read_text(encoding="utf-8")))
    completed: dict[str, dict[str, Any]] = dict(checkpoint.get("completed", {}))
    pending = [
        candidate
        for candidate in candidates
        if f"{group}:{spec_hash(candidate['spec'])}" not in completed
    ]
    candles = _load_window(
        Path(args.lake),
        args.instrument,
        args.timeframe,
        start=start,
        end=end,
    )
    zero = _zero_cost(args.instrument)
    normal = pessimistic_cost_model(args.config)
    started = time.monotonic()
    last_progress = started

    for offset in range(0, len(pending), max(1, args.batch_size)):
        batch = pending[offset : offset + max(1, args.batch_size)]
        for row in _evaluate(
            batch,
            candles,
            args.instrument,
            args.timeframe,
            zero,
            normal,
        ):
            completed[f"{group}:{row['strategy_hash']}"] = row
        _write_json(
            checkpoint_path,
            {
                "started_at": checkpoint.get("started_at", _stamp()),
                "updated_at": _stamp(),
                "completed": completed,
                "group": group,
                "candidate_start": args.candidate_start,
                "candidate_end": args.candidate_end,
                "last_progress_seconds": time.monotonic() - started,
                "memory_bytes": _memory_bytes(),
            },
        )
        idle_seconds = time.monotonic() - last_progress
        last_progress = time.monotonic()
        memory = _memory_bytes()
        if memory is not None and memory > args.memory_limit_gb * 1024**3:
            _write_json(
                Path(args.progress),
                {
                    "status": "stopped",
                    "reason": "memory_limit",
                    "memory_bytes": memory,
                    "group": group,
                },
            )
            return 2
        if idle_seconds > args.no_progress_seconds:
            _write_json(
                Path(args.progress),
                {"status": "stopped", "reason": "no_progress", "group": group},
            )
            return 2
        _write_json(
            Path(args.progress),
            {
                "status": "running",
                "updated_at": _stamp(),
                "group": group,
                "candidate_start": args.candidate_start,
                "candidate_end": args.candidate_end,
                "completed": len(completed),
                "total": len(candidates),
                "memory_bytes": memory,
            },
        )
        gc.collect()

    rows = list(completed.values())
    summary = _group_summary(rows, min_trades=args.min_trades)
    payload = {
        "task": "T-143",
        "diagnostic_zero_cost": True,
        "partial_worker": True,
        "generated_at": _stamp(),
        "data_start_inclusive": start.isoformat(),
        "data_end_exclusive": end.isoformat(),
        "year_tables": "combined 2025-2026 window; no sealed months read",
        "order": [f"{args.timeframe}m"],
        "markets": [args.instrument],
        "staged_generations": args.staged_generations,
        "max_staged_parents": args.max_staged_parents,
        "minimum_trade_count": args.min_trades,
        "costs": {
            "zero": "spread=0, slippage=0",
            "normal": "configured pessimistic cost model",
        },
        "gate1_evaluated": False,
        "sealed_holdout_read": False,
        "broker_calls": 0,
        "orders": 0,
        "candidate_range": [args.candidate_start, args.candidate_end],
        "summary": {group: summary},
        "strategies": rows,
    }
    _write_json(Path(args.output), payload)
    _write_json(
        Path(args.progress),
        {
            "status": "completed",
            "updated_at": _stamp(),
            "strategies": len(completed),
            "output": str(args.output),
        },
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--config", type=Path, default=Path("config/strategy_lab.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2026-07-01")
    parser.add_argument("--instrument", choices=list(INSTRUMENTS), required=True)
    parser.add_argument("--timeframe", type=int, choices=(60, 5, 3, 1), required=True)
    parser.add_argument("--candidate-start", type=int, required=True)
    parser.add_argument("--candidate-end", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--staged-generations", type=int, default=3)
    parser.add_argument("--max-staged-parents", type=int, default=10)
    parser.add_argument("--min-trades", type=int, default=MIN_GROUP_TRADES)
    parser.add_argument("--memory-limit-gb", type=float, default=12.0)
    parser.add_argument("--no-progress-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    if args.staged_generations < 0 or args.max_staged_parents < 1:
        raise SystemExit("staged-generations must be non-negative and max-staged-parents positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
