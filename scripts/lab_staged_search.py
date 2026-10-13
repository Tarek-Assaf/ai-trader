#!/usr/bin/env python3
"""Staged mutate-and-retest search for one market and timeframe (docs/specs/staged_search.md).

1. Parents: near misses from existing lab results files (>= 300 trades, positive gross edge,
   best out-of-sample profit factor), rebuilt as declarative specs from the seed libraries.
2. Up to three generations of one-change mutations; children are ranked on the early training
   years and must also be profitable after costs on the later years and on the other market.
3. Survivors go through the unchanged Gate 1 pipeline once, with ``trials`` = every distinct
   spec ever evaluated (all stages, dropped variants included) plus the prior attempts on
   this market.  The sealed holdout is read only there, through the existing sealed reader.

No broker, no secrets; gates, costs and holdout handling are not modified.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trader.lab.ledger import ExperimentLedger, ExperimentRecord
from trader.lab.runner import (
    INSTRUMENTS,
    LabRunner,
    RunFileLock,
    holdout_months,
    load_histdata_candles,
    pessimistic_cost_model,
    strategy_lab_max_training_years,
)
from trader.lab.spec import StrategySpec
from trader.lab.staged import (
    StagedSearch,
    child_payload,
    generation_payload,
    spec_hash,
    split_windows,
    window_evaluator,
)

PARENT_MIN_TRADES = 300


class StagedFinalRunner(LabRunner):
    """The real runner; only idea provisioning is replaced (judging is unchanged)."""

    def __init__(self, *args: Any, specs: list[tuple[str, StrategySpec]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._specs = specs

    def _candidates(self, *args: Any, **kwargs: Any) -> list[tuple[str, Any]]:
        return list(self._specs)


def training_slice(candles: Any, sealed: set[tuple[int, int]], years: int | None) -> list[Any]:
    """The same training window the runner uses (80% of candles before the sealed months)."""
    if years is not None and sealed:
        first = min(sealed)
        cutoff = datetime(first[0] - years, first[1], 1, tzinfo=UTC)
        candles = tuple(item for item in candles if item.ts_utc >= cutoff)
    split = max(1, int(len(candles) * 0.8))
    return list(candles[:split])


def load_attempts(paths: list[Path], instrument: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows.extend(
            attempt
            for attempt in payload.get("attempts", [])
            if attempt.get("instrument") == instrument
        )
    return rows


def pick_parents(
    attempts: list[dict[str, Any]],
    pool: dict[str, StrategySpec],
    timeframe: int,
    limit: int,
) -> list[StrategySpec]:
    """Best near misses: enough trades and a positive gross edge, ranked by OOS profit factor."""
    best: dict[str, float] = {}
    for attempt in attempts:
        metrics = attempt.get("metrics", {})
        digest = attempt.get("strategy_hash")
        if attempt.get("timeframe") != timeframe or digest not in pool:
            continue
        if float(metrics.get("trades", 0)) < PARENT_MIN_TRADES:
            continue
        if float(metrics.get("diag_avg_gross_per_trade", 0.0)) <= 0:
            continue
        score = float(metrics.get("out_of_sample_profit_factor", 0.0))
        best[digest] = max(best.get(digest, 0.0), score)
    ranked = sorted(best, key=lambda key: best[key], reverse=True)[:limit]
    return [pool[digest] for digest in ranked]


def trial_ledger(hashes: set[str], start: datetime, end: datetime) -> ExperimentLedger:
    return ExperimentLedger(
        ExperimentRecord(
            strategy_hash=digest,
            parameters={"stage": "staged_search_trial"},
            data_start=start,
            data_end=end,
            metrics={},
            proposer="staged_search",
        )
        for digest in sorted(hashes)
    )


def main() -> int:
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except (OSError, PermissionError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--holdout", type=Path, default=Path("/workspace/data/holdout"))
    parser.add_argument("--config", type=Path, default=Path("config/strategy_lab.yaml"))
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--instrument", choices=list(INSTRUMENTS), required=True)
    parser.add_argument("--timeframe", type=int, required=True, choices=[1, 3, 5, 15, 60])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--max-parents", type=int, default=10)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--idea-sources", nargs="+", default=["seeds", "round_c", "round_d"])
    parser.add_argument("--workers", type=int)
    parser.add_argument("--evaluation-batch-size", type=int, default=50)
    parser.add_argument("--years", help="training history in years, or 'all'")
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--lock-file", type=Path, default=Path("/workspace/state/lab/run.lock"))
    parser.add_argument("--progress", type=Path, default=Path("/workspace/state/lab/progress.json"))
    parser.add_argument("--cache-root", type=Path, default=Path("/workspace/state/lab/cache"))
    args = parser.parse_args()

    if args.years is None:
        years: int | None = strategy_lab_max_training_years(args.config)
    elif args.years.lower() == "all":
        years = None
    else:
        years = int(args.years)
    out = args.out or Path(
        f"docs/evidence/staged_search_{args.instrument.lower()}_{args.timeframe}m.json"
    )
    other = next(name for name in INSTRUMENTS if name != args.instrument)
    started = datetime.now(UTC)

    def write_progress(message: str, status: str = "running") -> None:
        args.progress.parent.mkdir(parents=True, exist_ok=True)
        args.progress.write_text(
            json.dumps(
                {
                    "status": status,
                    "job": "staged_search",
                    "pid": os.getpid(),
                    "started_at": started.isoformat(),
                    "updated_at": datetime.now(UTC).isoformat(),
                    "phase": message,
                }
            ),
            encoding="utf-8",
        )

    def stopped() -> bool:
        return bool(args.stop_file and args.stop_file.exists())

    with RunFileLock(args.lock_file):
        write_progress("loading")
        sealed = holdout_months(args.holdout, INSTRUMENTS)
        series = {
            name: training_slice(
                load_histdata_candles(
                    args.lake,
                    name,
                    args.timeframe,
                    exclude_months=sealed,
                    cache_root=args.cache_root,
                ),
                sealed,
                years,
            )
            for name in (args.instrument, other)
        }
        runner = LabRunner(
            lake_root=args.lake,
            holdout_root=args.holdout,
            config_path=args.config,
            cache_root=args.cache_root,
            max_training_years=years,
        )
        pool = {
            spec_hash(spec): spec
            for _, spec in runner._candidates(
                args.instrument, args.timeframe, tuple(args.idea_sources), ()
            )
        }
        attempts = load_attempts(args.results, args.instrument)
        parents = pick_parents(attempts, pool, args.timeframe, args.max_parents)
        prior = {a["strategy_hash"] for a in attempts if a.get("strategy_hash")}
        report: dict[str, Any] = {
            "instrument": args.instrument,
            "timeframe": args.timeframe,
            "created_at": started.isoformat(),
            "spec": "docs/specs/staged_search.md",
            "gates_changed": False,
            "parents": len(parents),
            "prior_attempts_on_market": len(prior),
            "results_files": [str(path) for path in args.results],
        }
        if not parents:
            report["note"] = "no parents with >=300 trades and a positive gross edge"
        else:
            windows = split_windows(series[args.instrument], series[other])
            evaluator = window_evaluator(
                windows, pessimistic_cost_model(args.config), other_instrument=other
            )
            outcome = StagedSearch(
                evaluator,
                max_generations=args.generations,
                max_parents=args.max_parents,
                progress=write_progress,
                should_stop=stopped,
                prior_hashes=prior,
            ).run(parents)
            report["generations"] = [generation_payload(item) for item in outcome.generations]
            report["total_trials"] = outcome.trials
            ranked = sorted(
                outcome.survivors,
                key=lambda c: min(
                    c.later.net_per_trade if c.later else 0.0,
                    c.other.net_per_trade if c.other else 0.0,
                ),
                reverse=True,
            )[:50]
            report["survivors"] = [child_payload(child) for child in ranked]
            if ranked and not stopped():
                write_progress("final_gate1")
                seen: set[str] = set()
                specs: list[tuple[str, StrategySpec]] = []
                for child in ranked:
                    if child.hash not in seen:
                        seen.add(child.hash)
                        specs.append((f"staged:{child.label}", child.spec))
                ledger = trial_ledger(
                    outcome.trial_hashes,
                    series[args.instrument][0].ts_utc,
                    series[args.instrument][-1].ts_utc,
                )
                final = StagedFinalRunner(
                    lake_root=args.lake,
                    holdout_root=args.holdout,
                    config_path=args.config,
                    cache_root=args.cache_root,
                    max_training_years=years,
                    ledger=ledger,
                    specs=specs,
                )
                result = final.run(
                    instruments=[args.instrument],
                    timeframes=[args.timeframe],
                    idea_sources=["staged"],
                    max_ideas=max(1, len(specs)),
                    workers=args.workers,
                    force=True,
                    evaluation_batch_size=args.evaluation_batch_size,
                )
                payload = result.payload()
                report["final_gate1"] = {
                    "attempts": len(payload["attempts"]),
                    "passes": len(payload["survivors"]),
                    "attempt_details": [
                        {
                            "strategy_hash": row.get("strategy_hash"),
                            "source": row.get("source"),
                            "verdict": row.get("verdict"),
                            "reason": row.get("reason"),
                            "metrics": row.get("metrics"),
                        }
                        for row in payload["attempts"]
                    ],
                }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        write_progress("done", "completed")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
