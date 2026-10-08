#!/usr/bin/env python3
"""Run the offline Strategy Lab job; this script never contacts a broker."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from trader.lab.runner import (
    INSTRUMENTS,
    TIMEFRAMES,
    LabRunner,
    prepare_holdout,
    strategy_lab_holdout_months,
    strategy_lab_max_training_years,
)


def main() -> int:
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except (OSError, PermissionError):
            pass
    parser = argparse.ArgumentParser(description="Run Strategy Lab Gate 1 validation")
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--holdout", type=Path, default=Path("/workspace/data/holdout"))
    parser.add_argument("--results", type=Path, default=Path("/workspace/state/lab/results.json"))
    parser.add_argument("--config", type=Path, default=Path("config/strategy_lab.yaml"))
    parser.add_argument("--instruments", nargs="+", default=list(INSTRUMENTS))
    parser.add_argument("--timeframes", nargs="+", type=int, default=list(TIMEFRAMES))
    parser.add_argument("--idea-sources", nargs="+", default=["seeds", "patterns"])
    parser.add_argument("--max-ideas", type=int, default=100)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--years", help="training history in years, or 'all'")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--lock-file", type=Path, default=Path("/workspace/state/lab/run.lock"))
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--prepare-holdout", action="store_true")
    parser.add_argument(
        "--holdout-months",
        type=int,
        help="override config data.holdout_months for an explicit preparation run",
    )
    args = parser.parse_args()
    holdout_months = (
        args.holdout_months
        if args.holdout_months is not None
        else strategy_lab_holdout_months(args.config)
    )
    if args.years is None:
        max_training_years = strategy_lab_max_training_years(args.config)
    elif args.years.lower() == "all":
        max_training_years = None
    else:
        try:
            max_training_years = int(args.years)
        except ValueError as exc:
            raise SystemExit("--years must be a positive integer or all") from exc
        if max_training_years < 1:
            raise SystemExit("--years must be a positive integer or all")
    if args.prepare_holdout:
        copied = prepare_holdout(
            args.lake,
            args.holdout,
            instruments=args.instruments,
            months=holdout_months,
        )
        print(f"prepared holdout files: {len(copied)}")
        return 0
    if args.progress:
        args.progress.parent.mkdir(parents=True, exist_ok=True)
        args.progress.write_text(json.dumps({"status": "running"}), encoding="utf-8")
    runner = LabRunner(
        lake_root=args.lake,
        holdout_root=args.holdout,
        config_path=args.config,
        max_training_years=max_training_years,
        lock_path=args.lock_file,
    )
    try:
        result = runner.run(
            instruments=args.instruments,
            timeframes=args.timeframes,
            idea_sources=args.idea_sources,
            max_ideas=max(1, args.max_ideas),
            workers=args.workers,
            force=args.force,
            stop_file=args.stop_file,
            progress_path=args.progress,
        )
    except Exception as exc:
        if args.progress:
            args.progress.write_text(
                json.dumps({"status": "failed", "reason": type(exc).__name__}),
                encoding="utf-8",
            )
        raise
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(result.payload(), indent=2), encoding="utf-8")
    print(LabRunner.format_results(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
