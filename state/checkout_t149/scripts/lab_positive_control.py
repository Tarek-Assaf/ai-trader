#!/usr/bin/env python3
"""Positive control: can the unchanged lab and Gate 1 detect an edge that really exists?

Builds SYNTHETIC one-minute lakes (never real data) for the symbol NSXUSD in a temporary
directory, plants an edge worth a chosen multiple of the round-trip cost, then runs the
unchanged ``LabRunner`` / ``ValidationPipeline`` with the real ``config/strategy_lab.yaml``.
Also runs a 200-strategy null battery on the no-edge series to measure false passes.

No broker, no secrets, no real data; the real lab ledger is not touched.
See docs/specs/lab_positive_control.md.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trader.lab.positive_control import (
    ROUND_TRIP_COST_POINTS,
    calibrate_drift,
    generate_minutes,
    planted_strategy,
)
from trader.lab.runner import (
    LabRunner,
    prepare_holdout,
    process_memory_diagnostics,
    strategy_lab_holdout_months,
)

INSTRUMENT = "NSXUSD"
TIMEFRAME = 5
MULTIPLES = (2.0, 1.0, 0.5, 0.0, -0.5)
REQUIRED_PASS = {2.0}
REQUIRED_FAIL = {0.0, -0.5}


class ControlRunner(LabRunner):
    """The real runner; only idea provisioning is replaced (judging is unchanged)."""

    def __init__(self, *args: Any, mode: str = "planted", null_count: int = 200, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.mode = mode
        self.null_count = null_count

    def _candidates(
        self,
        instrument: str,
        timeframe: int,
        sources: Sequence[str],
        candles: Sequence[Any],
        **kwargs: Any,
    ) -> list[tuple[str, Any]]:
        if self.mode == "planted":
            return [("control:planted", planted_strategy(instrument, timeframe))]
        pool = super()._candidates(instrument, timeframe, ("seeds", "round_c"), candles, **kwargs)
        step = max(1, len(pool) // self.null_count)
        return [("null:" + source, spec) for source, spec in pool[::step][: self.null_count]]


def write_lake(candles: Sequence[Any], lake: Path) -> int:
    import pyarrow as pa  # type: ignore[import-untyped,import-not-found,unused-ignore]
    import pyarrow.parquet as pq  # type: ignore[import-untyped,import-not-found,unused-ignore]

    by_month: dict[tuple[int, int], list[Any]] = {}
    for candle in candles:
        by_month.setdefault((candle.ts_utc.year, candle.ts_utc.month), []).append(candle)
    for (year, month), rows in by_month.items():
        folder = lake / INSTRUMENT / "1m" / f"{year:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        table = pa.table(
            {
                "ts_utc": pa.array([r.ts_utc for r in rows], type=pa.timestamp("us", tz="UTC")),
                "open": [r.open for r in rows],
                "high": [r.high for r in rows],
                "low": [r.low for r in rows],
                "close": [r.close for r in rows],
                "volume": [r.volume for r in rows],
            }
        )
        pq.write_table(table, folder / f"{month:02d}.parquet")
    return len(by_month)


def summarize(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for attempt in payload["attempts"]:
        rows.append(
            {
                "source": attempt["source"],
                "verdict": attempt["verdict"],
                "reason": attempt["reason"],
                "metrics": attempt["metrics"],
            }
        )
    return rows


def run_series(
    name: str,
    root: Path,
    *,
    mode: str,
    config: Path,
    workers: int,
    null_count: int,
) -> dict[str, Any]:
    holdout = root / "holdout"
    prepare_holdout(
        root / "lake",
        holdout,
        instruments=[INSTRUMENT],
        months=strategy_lab_holdout_months(config),
    )
    runner = ControlRunner(
        lake_root=root / "lake",
        holdout_root=holdout,
        config_path=config,
        cache_root=root / "cache",
        max_training_years=None,
        lock_path=root / "run.lock",
        mode=mode,
        null_count=null_count,
    )
    started = time.monotonic()
    result = runner.run(
        instruments=[INSTRUMENT],
        timeframes=[TIMEFRAME],
        idea_sources=["positive_control"],
        max_ideas=max(1, null_count),
        workers=workers,
        force=True,
        evaluation_batch_size=50 if mode == "null" else None,
    )
    payload = result.payload()
    attempts = summarize(payload)
    return {
        "name": name,
        "mode": mode,
        "seconds": round(time.monotonic() - started, 1),
        "memory": process_memory_diagnostics(),
        "attempts": attempts,
        "passes": sum(1 for a in attempts if a["verdict"] == "pass"),
        "survivors": len(payload["survivors"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("docs/evidence/positive_control.json"))
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config/strategy_lab.yaml"))
    parser.add_argument("--days", type=int, default=1044, help="trading days per series (~4 years)")
    parser.add_argument("--calibration-days", type=int, default=250)
    parser.add_argument("--seed", type=int, default=20261012)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--null-count", type=int, default=200)
    parser.add_argument("--multiples", nargs="+", type=float, default=list(MULTIPLES))
    parser.add_argument("--keep", action="store_true", help="keep the synthetic lakes")
    args = parser.parse_args()

    base = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="poscontrol-"))
    base.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "spec": "docs/specs/lab_positive_control.md",
        "synthetic": True,
        "instrument_symbol": INSTRUMENT,
        "timeframe_minutes": TIMEFRAME,
        "round_trip_cost_points": ROUND_TRIP_COST_POINTS,
        "seed": args.seed,
        "days_per_series": args.days,
        "gates_changed": False,
        "series": [],
    }
    null_root: Path | None = None
    try:
        for index, multiple in enumerate(args.multiples):
            label = f"m={multiple:+.1f}"
            root = base / f"series_{index}"
            if multiple == 0.0:
                calibration: dict[str, Any] = {"drift_per_minute": 0.0, "note": "pure null"}
                drift = 0.0
            else:
                calibration = calibrate_drift(
                    multiple * ROUND_TRIP_COST_POINTS,
                    seed=args.seed,
                    days=min(args.calibration_days, args.days),
                )
                drift = float(calibration["drift_per_minute"])
            candles = generate_minutes(args.seed, args.days, drift=drift, plant=True)
            months = write_lake(candles, root / "lake")
            del candles
            entry = run_series(
                label,
                root,
                mode="planted",
                config=args.config,
                workers=args.workers,
                null_count=args.null_count,
            )
            entry["multiple_of_cost"] = multiple
            entry["calibration"] = calibration
            entry["months_written"] = months
            attempt = entry["attempts"][0] if entry["attempts"] else {}
            entry["verdict"] = attempt.get("verdict", "no_attempt")
            evidence["series"].append(entry)
            print(f"{label}: verdict={entry['verdict']} reason={attempt.get('reason', '')}")
            if multiple == 0.0:
                null_root = root
            else:
                shutil.rmtree(root / "lake", ignore_errors=True)
        if null_root is not None:
            battery = run_series(
                "null battery",
                null_root,
                mode="null",
                config=args.config,
                workers=args.workers,
                null_count=args.null_count,
            )
            evidence["null_battery"] = battery
            print(f"null battery: {battery['passes']} passes of {len(battery['attempts'])}")
    finally:
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)

    by_multiple = {s["multiple_of_cost"]: s["verdict"] for s in evidence["series"]}
    evidence["expected"] = {
        "m_2_passes": by_multiple.get(2.0) == "pass",
        "null_and_trap_fail": all(
            by_multiple.get(m, "fail") != "pass" for m in REQUIRED_FAIL if m in by_multiple
        ),
        "null_battery_passes": evidence.get("null_battery", {}).get("passes"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    print(json.dumps(evidence["expected"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
