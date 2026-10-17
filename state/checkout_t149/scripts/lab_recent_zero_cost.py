#!/usr/bin/env python3
"""Rebuild recent Round D and staged strategies for a zero-cost diagnostic.

This script is intentionally independent of the validation pipeline.  It reads
only the training lake, evaluates declarative strategies with zero and configured
costs, and never opens the sealed holdout or writes a trial ledger.  The default
window is 2025-01-01 through 2026-07-01 (exclusive), matching the pre-holdout
data boundary used for the T-143 run.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trader.lab.backtest import CostModel, run_backtest
from trader.lab.engine import IndicatorCache
from trader.lab.runner import load_histdata_candles, pessimistic_cost_model, tune_variants
from trader.lab.seeds import round_d_seed_specs
from trader.lab.staged import mutations, spec_hash

ORDER = (60, 5, 3, 1)
INSTRUMENTS = ("NSXUSD", "XAUUSD")
MIN_GROUP_TRADES = 300


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    os.replace(temporary, path)


def _memory_bytes() -> int | None:
    """Return the cgroup memory figure when available, otherwise None."""
    for filename in (
        "/sys/fs/cgroup/memory.current",
        "/sys/fs/cgroup/memory/memory.usage_in_bytes",
    ):
        try:
            return int(Path(filename).read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            continue
    return None


def _zero_cost(instrument: str) -> CostModel:
    return CostModel(
        spread_points={instrument: 0.0},
        slippage_points={instrument: 0.0},
        provisional=False,
    )


def _trade_stats(result: Any) -> dict[str, Any]:
    values = [float(trade.net_pnl) for trade in result.trades]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return {
        "trades": len(values),
        "average_trade_points": sum(values) / len(values) if values else 0.0,
        "best_trade_points": max(values) if values else 0.0,
        "worst_trade_points": min(values) if values else 0.0,
        "net_points": sum(values),
        "profit_factor": gains / losses if losses else (None if not gains else "inf"),
        "max_drawdown_points": float(result.max_drawdown),
    }


def _describe(spec: Any) -> str:
    entries: list[str] = []
    for condition in spec.entry_conditions:
        threshold = getattr(condition, "threshold", "")
        indicator = getattr(condition, "indicator", "?")
        operator = getattr(condition, "operator", "?")
        entries.append(f"{indicator} {operator} {threshold}")
    exits: list[str] = []
    for rule in spec.exit_rules:
        kind = getattr(rule, "kind", "?")
        if kind == "time_stop":
            exits.append(f"time_stop {getattr(rule, 'bars', '?')} bars")
        else:
            text = f"{kind} {getattr(rule, 'distance', '?')} {getattr(rule, 'unit', 'points')}"
            exits.append(text)
    sessions = ",".join(getattr(spec, "session_filters", ())) or "any"
    return (
        f"{spec.direction}; sessions={sessions}; entry: {'; '.join(entries)}; "
        f"exits: {'; '.join(exits)}"
    )


def _candidate_specs(
    instrument: str,
    timeframe: int,
    *,
    staged_generations: int,
    max_staged_parents: int,
) -> list[dict[str, Any]]:
    """Build Round D variants and deterministic staged mutation variants."""
    round_d: list[dict[str, Any]] = []
    for seed, base in round_d_seed_specs(instrument, timeframe):
        for variant_index, spec in enumerate(tune_variants(base)):
            round_d.append(
                {
                    "source": "round_d",
                    "source_name": seed.name,
                    "variant": variant_index,
                    "spec": spec,
                }
            )

    # T-139 used a bounded parent pool and the fixed mutation menu.  Rebuild
    # that menu from code, rather than reading prior result JSON files.  Hash
    # ordering keeps the selected parent set and checkpoint keys reproducible.
    parents = sorted(round_d, key=lambda row: spec_hash(row["spec"]))[:max_staged_parents]
    seen = {spec_hash(row["spec"]) for row in round_d}
    staged: list[dict[str, Any]] = []
    frontier = parents
    for generation in range(1, staged_generations + 1):
        next_frontier: list[dict[str, Any]] = []
        for parent in frontier:
            for label, child in mutations(parent["spec"]):
                digest = spec_hash(child)
                if digest in seen:
                    continue
                seen.add(digest)
                row = {
                    "source": "staged",
                    "source_name": label,
                    "variant": generation,
                    "parent_hash": spec_hash(parent["spec"]),
                    "generation": generation,
                    "spec": child,
                }
                staged.append(row)
                next_frontier.append(row)
        frontier = next_frontier
    return round_d + staged


def _months_at_or_after(end_date: datetime) -> set[tuple[int, int]]:
    """Exclude lake files at and after the explicit pre-holdout boundary."""
    result: set[tuple[int, int]] = set()
    for year in range(end_date.year, end_date.year + 3):
        for month in range(1, 13):
            if (year, month) >= (end_date.year, end_date.month):
                result.add((year, month))
    return result


def _load_window(
    lake: Path,
    instrument: str,
    timeframe: int,
    *,
    start: datetime,
    end: datetime,
) -> tuple[Any, ...]:
    # The explicit month exclusion prevents the loader from reading sealed or
    # post-window files.  No holdout path or sealed reader is touched.
    candles = load_histdata_candles(
        lake,
        instrument,
        timeframe,
        exclude_months=_months_at_or_after(end),
    )
    return tuple(candle for candle in candles if start <= candle.ts_utc < end)


def _empty_stats() -> dict[str, Any]:
    return {
        "trades": 0,
        "average_trade_points": 0.0,
        "best_trade_points": 0.0,
        "worst_trade_points": 0.0,
        "net_points": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_points": 0.0,
    }


def _evaluate(
    candidates: Iterable[dict[str, Any]],
    candles: tuple[Any, ...],
    instrument: str,
    timeframe: int,
    zero: CostModel,
    normal: CostModel,
) -> list[dict[str, Any]]:
    cache = IndicatorCache.from_candles(candles, timeframe) if candles else None
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        spec = candidate["spec"]
        if candles:
            zero_result = run_backtest(spec, candles, cost_model=zero, indicator_cache=cache)
            normal_result = run_backtest(spec, candles, cost_model=normal, indicator_cache=cache)
            zero_stats = _trade_stats(zero_result)
            normal_stats = _trade_stats(normal_result)
        else:
            zero_stats = _empty_stats()
            normal_stats = _empty_stats()
        rows.append(
            {
                "instrument": instrument,
                "timeframe": timeframe,
                "source": candidate["source"],
                "source_name": candidate["source_name"],
                "variant": candidate.get("variant"),
                "generation": candidate.get("generation"),
                "parent_hash": candidate.get("parent_hash"),
                "strategy_hash": spec_hash(spec),
                "rules": _describe(spec),
                "diagnostic_zero_cost": True,
                "zero_cost": zero_stats,
                "normal_cost": normal_stats,
                "sealed_holdout_read": False,
                "gate1_evaluated": False,
            }
        )
    return rows


def _group_summary(rows: list[dict[str, Any]], *, min_trades: int) -> dict[str, Any]:
    def aggregate(name: str) -> dict[str, Any]:
        stats = [row[name] for row in rows]
        trades = sum(int(item["trades"]) for item in stats)
        net = sum(float(item["net_points"]) for item in stats)
        values = [float(item["best_trade_points"]) for item in stats if item["trades"]]
        worst = [float(item["worst_trade_points"]) for item in stats if item["trades"]]
        return {
            "total_trades": trades,
            "average_trade_points": net / trades if trades else 0.0,
            "best_trade_points": max(values, default=0.0),
            "worst_trade_points": min(worst, default=0.0),
            "profitable_strategies": sum(1 for item in stats if float(item["net_points"]) > 0),
            "strategies_meeting_minimum_trade_count": sum(
                1 for item in stats if int(item["trades"]) >= min_trades
            ),
        }

    def pf_sort(row: dict[str, Any]) -> float:
        value = row["zero_cost"]["profit_factor"]
        if value == "inf":
            return float("inf")
        return float(value) if value is not None else 0.0

    top = sorted(
        rows,
        key=lambda row: (pf_sort(row), float(row["zero_cost"]["net_points"])),
        reverse=True,
    )[:10]
    return {
        "strategies_rebuilt": len(rows),
        "by_source": {
            source: sum(1 for row in rows if row["source"] == source)
            for source in ("round_d", "staged")
        },
        "zero_cost": aggregate("zero_cost"),
        "normal_cost": aggregate("normal_cost"),
        "top_10_zero_cost_profit_factor": [
            {
                "strategy_hash": row["strategy_hash"],
                "source": row["source"],
                "rules": row["rules"],
                "trades": row["zero_cost"]["trades"],
                "profit_factor": row["zero_cost"]["profit_factor"],
                "net_points": row["zero_cost"]["net_points"],
                "max_drawdown_points": row["zero_cost"]["max_drawdown_points"],
            }
            for row in top
        ],
    }


def run(args: argparse.Namespace) -> int:
    start = datetime.fromisoformat(args.start_date).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end_date).replace(tzinfo=UTC)
    if end <= start:
        raise ValueError("end date must be after start date")
    normal_models = pessimistic_cost_model(args.config)
    checkpoint_path = Path(args.checkpoint)
    checkpoint: dict[str, Any] = {"completed": {}, "started_at": _stamp()}
    if checkpoint_path.exists():
        checkpoint.update(json.loads(checkpoint_path.read_text(encoding="utf-8")))
    completed: dict[str, dict[str, Any]] = dict(checkpoint.get("completed", {}))
    summaries: dict[str, Any] = dict(checkpoint.get("summaries", {}))
    candidates_by_group: dict[str, list[dict[str, Any]]] = {}
    started = time.monotonic()
    last_progress = started
    timeframes = tuple(args.timeframes)
    instruments = tuple(args.instruments)
    for timeframe in timeframes:
        for instrument in instruments:
            group = f"{instrument}:{timeframe}"
            candidates = _candidate_specs(
                instrument,
                timeframe,
                staged_generations=args.staged_generations,
                max_staged_parents=args.max_staged_parents,
            )
            candidates_by_group[group] = candidates
            candles = _load_window(Path(args.lake), instrument, timeframe, start=start, end=end)
            pending = [
                candidate
                for candidate in candidates
                if f"{group}:{spec_hash(candidate['spec'])}" not in completed
            ]
            zero = _zero_cost(instrument)
            for offset in range(0, len(pending), max(1, args.batch_size)):
                batch = pending[offset : offset + max(1, args.batch_size)]
                for row in _evaluate(batch, candles, instrument, timeframe, zero, normal_models):
                    completed[f"{group}:{row['strategy_hash']}"] = row
                _write_json(
                    checkpoint_path,
                    {
                        "started_at": checkpoint.get("started_at", _stamp()),
                        "updated_at": _stamp(),
                        "completed": completed,
                        "summaries": summaries,
                        "last_group": group,
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
                        "completed": len(completed),
                        "total": sum(len(item) for item in candidates_by_group.values()),
                        "memory_bytes": _memory_bytes(),
                    },
                )
                gc.collect()
            rows = [row for key, row in completed.items() if key.startswith(f"{group}:")]
            summaries[group] = _group_summary(rows, min_trades=args.min_trades)
            _write_json(
                checkpoint_path,
                {"updated_at": _stamp(), "completed": completed, "summaries": summaries},
            )

    payload = {
        "task": "T-143",
        "diagnostic_zero_cost": True,
        "generated_at": _stamp(),
        "data_start_inclusive": start.isoformat(),
        "data_end_exclusive": end.isoformat(),
        "year_tables": "combined 2025-2026 window; no sealed months read",
        "order": [f"{timeframe}m" for timeframe in timeframes],
        "markets": list(instruments),
        "staged_generations": args.staged_generations,
        "max_staged_parents": args.max_staged_parents,
        "minimum_trade_count": args.min_trades,
        "costs": {"zero": "spread=0, slippage=0", "normal": "configured pessimistic cost model"},
        "gate1_evaluated": False,
        "sealed_holdout_read": False,
        "broker_calls": 0,
        "orders": 0,
        "summary": summaries,
        "strategies": list(completed.values()),
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
    parser.add_argument(
        "--output", type=Path, default=Path("/workspace/state/lab/t143_recent_zero_cost.json")
    )
    parser.add_argument(
        "--checkpoint", type=Path, default=Path("/workspace/state/lab/t143_checkpoint.json")
    )
    parser.add_argument(
        "--progress", type=Path, default=Path("/workspace/state/lab/t143_progress.json")
    )
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2026-07-01")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--staged-generations", type=int, default=3)
    parser.add_argument("--max-staged-parents", type=int, default=10)
    parser.add_argument("--min-trades", type=int, default=MIN_GROUP_TRADES)
    parser.add_argument(
        "--instruments", nargs="+", choices=list(INSTRUMENTS), default=list(INSTRUMENTS)
    )
    parser.add_argument(
        "--timeframes", nargs="+", type=int, choices=list(ORDER), default=list(ORDER)
    )
    parser.add_argument("--memory-limit-gb", type=float, default=12.0)
    parser.add_argument("--no-progress-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    if args.staged_generations < 0 or args.max_staged_parents < 1:
        raise SystemExit("staged-generations must be non-negative and max-staged-parents positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
