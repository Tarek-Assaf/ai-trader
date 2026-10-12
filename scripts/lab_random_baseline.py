#!/usr/bin/env python3
"""Random-entry baseline for the best Strategy Lab attempts (read-only, training months only)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from trader.lab.random_baseline import random_entry_baseline
from trader.lab.runner import (
    holdout_months,
    load_histdata_candles,
    pessimistic_cost_model,
    tune_variants,
)
from trader.lab.seeds import (
    instrument_seed_specs,
    level_seed_specs,
    round_b_seed_specs,
    round_c_seed_specs,
    seed_specs,
    slow_seed_specs,
    swing_seed_specs,
)
from trader.lab.validator import strategy_hash


def _specs_by_hash(instrument: str, timeframe: int) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for seed, spec in (
        *seed_specs(instrument, timeframe),
        *slow_seed_specs(instrument, timeframe),
        *swing_seed_specs(instrument, timeframe),
        *level_seed_specs(instrument, timeframe),
        *instrument_seed_specs(instrument, timeframe),
        *round_b_seed_specs(instrument, timeframe),
        *round_c_seed_specs(instrument, timeframe),
    ):
        for variant in tune_variants(spec):
            found[strategy_hash(variant.model_dump(mode="json"))] = (seed.name, variant)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Random-entry baseline for top attempts")
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--holdout", type=Path, default=Path("/workspace/data/holdout"))
    parser.add_argument("--out", type=Path, default=Path("docs/evidence/random_baseline.json"))
    parser.add_argument("--top", type=int, default=8, help="best attempts per instrument/timeframe")
    parser.add_argument("--min-trades", type=int, default=300)
    parser.add_argument("--trials", type=int, default=60)
    args = parser.parse_args()
    sealed = holdout_months(args.holdout)
    cost_model = pessimistic_cost_model()
    report: list[dict[str, object]] = []
    for path in args.results:
        attempts = json.loads(path.read_text(encoding="utf-8")).get("attempts", [])
        groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for item in attempts:
            metrics = item.get("metrics", {})
            if (metrics.get("trades") or 0) >= args.min_trades and metrics.get(
                "diag_oos_mean_pnl"
            ) is not None:
                groups.setdefault((item["instrument"], int(item["timeframe"])), []).append(item)
        for (instrument, timeframe), items in sorted(groups.items()):
            items.sort(
                key=lambda row: row["metrics"].get("diag_oos_mean_pnl") or -1e9, reverse=True
            )
            specs = _specs_by_hash(instrument, timeframe)
            candles = load_histdata_candles(args.lake, instrument, timeframe, exclude_months=sealed)
            for item in items[: args.top]:
                known = specs.get(item["strategy_hash"])
                if known is None:
                    continue
                name, spec = known
                row = random_entry_baseline(
                    spec, candles, cost_model=cost_model, trials=args.trials
                )
                report.append(
                    {
                        "instrument": instrument,
                        "timeframe": timeframe,
                        "source": item["source"],
                        "name": name,
                        "baseline": row,
                    }
                )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(report)} strategies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
