#!/usr/bin/env python3
"""Zero-cost diagnostic over the Strategy Lab's stored candidate attempts.

This is deliberately a diagnostic: it never writes the gate/config/holdout and
never feeds trial or promotion state.  Candidate definitions are regenerated
from the deterministic seed families and selected by the hashes recorded in the
append-only result files.
"""

from __future__ import annotations

import argparse
import gc
import glob
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from trader.lab.backtest import CostModel, run_backtest
from trader.lab.ledger import ExperimentLedger
from trader.lab.pipeline import ValidationPipeline
from trader.lab.runner import (
    LabRunner,
    _holdout_loader,
    _parquet_files,
    load_histdata_candles,
    walk_forward_sizes,
)
from trader.lab.validator import Gate1Config, open_sealed_reader, strategy_hash

ORDER = (60, 5, 3, 1)
INSTRUMENTS = ("NSXUSD", "XAUUSD")


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _load_attempts(root: Path) -> dict[tuple[str, int], dict[str, dict[str, Any]]]:
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for filename in sorted(glob.glob(str(root / "results*.json"))):
        if Path(filename).name.startswith("results_zero_cost"):
            continue
        try:
            payload = json.loads(Path(filename).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for row in payload.get("attempts", []):
            try:
                key = (str(row["instrument"]), int(row["timeframe"]))
                digest = str(row["strategy_hash"])
            except (KeyError, TypeError, ValueError):
                continue
            grouped[key].setdefault(digest, {**row, "source_file": Path(filename).name})
    return grouped


def _describe(spec: Any) -> str:
    entries = []
    for condition in spec.entry_conditions:
        threshold = getattr(condition, "threshold", "")
        indicator = getattr(condition, "indicator", "?")
        operator = getattr(condition, "operator", "?")
        entries.append(f"{indicator} {operator} {threshold}")
    exits = []
    for rule in spec.exit_rules:
        if getattr(rule, "kind", "") == "time_stop":
            exits.append(f"time_stop {getattr(rule, 'bars', '?')} bars")
        else:
            kind = getattr(rule, "kind", "?")
            distance = getattr(rule, "distance", "?")
            unit = getattr(rule, "unit", "points")
            text = f"{kind} {distance} {unit}"
            if hasattr(rule, "activation_distance"):
                text += f" activation {rule.activation_distance}"
            exits.append(text)
    return f"{spec.direction}; entry: {', '.join(entries)}; exits: {', '.join(exits)}"


def _zero_cost(instrument: str) -> CostModel:
    # Explicit zero mappings make the diagnostic self-documenting and avoid
    # accidentally inheriting configured spread/slippage values.
    return CostModel(
        spread_points={instrument: 0.0},
        slippage_points={instrument: 0.0},
        provisional=False,
    )


def _trade_stats(result: Any) -> dict[str, Any]:
    values = [float(item.gross_pnl) for item in result.trades]
    return {
        "trades": len(values),
        "average_profit_per_trade_points": sum(values) / len(values) if values else 0.0,
        "best_trade_points": max(values) if values else 0.0,
        "worst_trade_points": min(values) if values else 0.0,
    }


def _with_cost_average(row: dict[str, Any]) -> float | None:
    metrics = row.get("metrics") or {}
    trades = float(metrics.get("trades", 0) or 0)
    if trades <= 0:
        return 0.0
    if metrics.get("diag_net_pnl") is not None:
        return float(metrics["diag_net_pnl"]) / trades
    gross = metrics.get("diag_avg_gross_per_trade")
    costs = metrics.get("diag_avg_cost_per_trade")
    if gross is not None and costs is not None:
        return float(gross) - float(costs)
    return None


def run(args: argparse.Namespace) -> int:
    state_root = Path(args.state_root)
    checkpoint_path = Path(args.checkpoint)
    progress_path = Path(args.progress)
    output_path = Path(args.output)
    attempts = _load_attempts(state_root)
    total_trials = max(1, len({digest for group in attempts.values() for digest in group}))
    checkpoint: dict[str, Any] = {
        "completed": {},
        "started_at": _stamp(),
        "total_trials": total_trials,
    }
    if checkpoint_path.exists():
        try:
            checkpoint.update(json.loads(checkpoint_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    rows: list[dict[str, Any]] = list(checkpoint.get("rows", []))
    done = set(checkpoint.get("completed", {}))
    runner = LabRunner(
        lake_root=args.lake,
        holdout_root=args.holdout,
        config_path=args.config,
        cache_root=args.cache,
        max_training_years=args.years,
    )
    config = Gate1Config.from_config(args.config)
    summary: dict[str, dict[str, Any]] = {}
    for timeframe in ORDER:
        for instrument in INSTRUMENTS:
            group_key = f"{instrument}:{timeframe}"
            targets = attempts.get((instrument, timeframe), {})
            if not targets:
                summary[group_key] = {"strategies_evaluated": 0, "missing": True}
                continue
            candles = load_histdata_candles(args.lake, instrument, timeframe, cache_root=args.cache)
            if args.years is not None:
                candles = tuple(candles[-args.years * 365 * 24 * 60 // timeframe :])
            split = max(1, int(len(candles) * 0.8))
            train, evaluation = candles[:split], candles[split:]
            train_bars, test_bars, step_bars = walk_forward_sizes(len(train))
            candidates = runner._candidates(
                instrument, timeframe, ("seeds", "round_c", "round_d"), train
            )
            by_hash: dict[str, tuple[str, Any]] = {}
            for source, spec in candidates:
                by_hash.setdefault(strategy_hash(spec.model_dump(mode="json")), (source, spec))
            holdout_files = _parquet_files(Path(args.holdout), instrument)
            holdout_path = holdout_files[0] if holdout_files else None
            if holdout_path is None:
                raise RuntimeError(f"no sealed holdout files for {instrument}")
            ledger = ExperimentLedger()
            reader = open_sealed_reader(args.holdout, ledger)
            zero = _zero_cost(instrument)
            pipeline = ValidationPipeline(
                ledger=ledger, config=config, cost_model=zero, cost_stress_pct=0.0
            )
            counts = Counter()
            group_rows: list[dict[str, Any]] = [
                row
                for row in rows
                if row.get("instrument") == instrument
                and int(row.get("timeframe", -1)) == timeframe
            ]
            missing: list[str] = []
            pending: list[tuple[str, dict[str, Any], str, Any]] = []
            for digest, stored in targets.items():
                key = f"{group_key}:{digest}"
                if key in done:
                    continue
                item = by_hash.get(digest)
                if item is None:
                    missing.append(digest)
                    done.add(key)
                    checkpoint.setdefault("missing_definitions", []).append(key)
                    continue
                source, spec = item
                pending.append((digest, stored, source, spec))
            batch_size = max(1, args.batch_size)
            for offset in range(0, len(pending), batch_size):
                batch = pending[offset : offset + batch_size]
                decisions = pipeline.run(
                    [item[3] for item in batch],
                    train,
                    holdout_reader=reader,
                    holdout_path=holdout_path,
                    holdout_loader=partial(
                        _holdout_loader,
                        root=Path(args.holdout),
                        instrument=instrument,
                        timeframe=timeframe,
                    ),
                    out_of_sample=evaluation,
                    train_bars=train_bars,
                    test_bars=test_bars,
                    step_bars=step_bars,
                    workers=max(1, args.workers),
                    trials=total_trials,
                )
                decisions_by_hash = {decision.strategy_hash: decision for decision in decisions}
                for digest, stored, source, spec in batch:
                    decision = decisions_by_hash[digest]
                    full = run_backtest(
                        spec,
                        train,
                        cost_model=zero,
                        train_bars=train_bars,
                        test_bars=test_bars,
                        step_bars=step_bars,
                    )
                    stats = _trade_stats(full)
                    reasons = list(decision.validation.reasons)
                    counts.update(reasons)
                    row = {
                        "instrument": instrument,
                        "timeframe": timeframe,
                        "strategy_hash": digest,
                        "source": stored.get("source", source),
                        "rules": _describe(spec),
                        "diagnostic_zero_cost": True,
                        "gate1_pass": bool(decision.validation.passed),
                        "gate1_failure_reasons": reasons,
                        "original_trial_count": total_trials,
                        "zero_cost_metrics": dict(decision.metrics),
                        "zero_cost_trade_stats": stats,
                        "stored_with_cost_average_per_trade": _with_cost_average(stored),
                    }
                    group_rows.append(row)
                    rows.append(row)
                    done.add(f"{group_key}:{digest}")
                checkpoint.update(
                    {"updated_at": _stamp(), "completed": {k: True for k in done}, "rows": rows}
                )
                _write_json(checkpoint_path, checkpoint)
                _write_json(
                    progress_path,
                    {
                        "status": "running",
                        "updated_at": _stamp(),
                        "group": group_key,
                        "completed": len(done),
                        "total": sum(len(group) for group in attempts.values()),
                    },
                )
                gc.collect()
            passes = [r for r in group_rows if r["gate1_pass"]]
            summary[group_key] = {
                "strategies_evaluated": len(targets),
                "missing_strategy_definitions": len(missing),
                "total_trades": sum(int(r["zero_cost_trade_stats"]["trades"]) for r in group_rows),
                "average_profit_per_trade_points": (
                    sum(
                        float(r["zero_cost_trade_stats"]["average_profit_per_trade_points"])
                        for r in group_rows
                    )
                    / len(group_rows)
                    if group_rows
                    else 0.0
                ),
                "best_trade_points": max(
                    (float(r["zero_cost_trade_stats"]["best_trade_points"]) for r in group_rows),
                    default=0.0,
                ),
                "worst_trade_points": min(
                    (float(r["zero_cost_trade_stats"]["worst_trade_points"]) for r in group_rows),
                    default=0.0,
                ),
                "gate1_passes": len(passes),
                "gate1_failure_counts": dict(counts),
            }
    payload = {
        "diagnostic_zero_cost": True,
        "generated_at": _stamp(),
        "cost_multiplier": 0.0,
        "order": [f"{tf}m" for tf in ORDER],
        "markets": list(INSTRUMENTS),
        "original_trial_count": total_trials,
        "summary": summary,
        "passes": [row for row in rows if row.get("gate1_pass")],
        "strategies": rows,
        "missing_definitions": sorted(set(checkpoint.get("missing_definitions", []))),
    }
    _write_json(output_path, payload)
    _write_json(
        progress_path,
        {
            "status": "completed",
            "updated_at": _stamp(),
            "strategies": len(rows),
            "output": str(output_path),
        },
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, default=Path("/workspace/state/lab"))
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--holdout", type=Path, default=Path("/workspace/data/holdout"))
    parser.add_argument("--cache", type=Path, default=Path("/workspace/state/lab/cache"))
    parser.add_argument("--config", type=Path, default=Path("config/strategy_lab.yaml"))
    parser.add_argument(
        "--output", type=Path, default=Path("/workspace/state/lab/results_zero_cost.json")
    )
    parser.add_argument(
        "--checkpoint", type=Path, default=Path("/workspace/state/lab/t142_checkpoint.json")
    )
    parser.add_argument(
        "--progress", type=Path, default=Path("/workspace/state/lab/t142_progress.json")
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--years", type=int, default=6)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
