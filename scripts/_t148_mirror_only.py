"""One-off mirror_full_v2 rerun used to refresh T-148 evidence."""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from scripts.lab_by_year import PERIODS, _load_window
from scripts.lab_t148_exit_study import (
    END_HOLDOUT,
    GROUPS,
    simulate,
    stats,
    tai_entries_full_inversion,
)


def run_group(instrument: str, timeframe: int) -> tuple[str, dict[str, object]]:
    candles = _load_window(
            Path("/workspace/data/lake"),
            instrument,
            timeframe,
            start=PERIODS["2022"][0],
            end=END_HOLDOUT,
        )
    chart, entries = tai_entries_full_inversion(candles, timeframe)
    cost = 4.0 if instrument == "NSXUSD" else 1.6
    periods: dict[str, object] = {}
    for period, (start, end) in PERIODS.items():
        selected = [entry for entry in entries if start <= entry.time < end]
        closed = simulate(chart, selected, "E0")
        periods[period] = {
            "zero_cost": stats(closed, chart, 0.0),
            "normal_cost": stats(closed, chart, cost),
        }
    return f"{instrument}:{timeframe}", {
            "instrument": instrument,
            "timeframe": timeframe,
            "bars": len(chart),
            "entries": len(entries),
            "periods": periods,
        }


def main() -> None:
    output = Path("/workspace/state/lab/t148_mirror_v2.json")
    payload: dict[str, object] = {
        "task": "T-148-mirror_full_v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "diagnostic_only": True,
        "sealed_holdout_read": False,
        "gate1_evaluated": False,
        "broker_calls": 0,
        "orders": 0,
        "groups": {},
    }


def run_group_unpack(group: tuple[str, int]) -> tuple[str, dict[str, object]]:
    return run_group(*group)
    groups = payload["groups"]
    assert isinstance(groups, dict)
    with ProcessPoolExecutor(max_workers=4) as pool:
        for key, result in pool.map(run_group_unpack, GROUPS):
            groups[key] = result
            print(f"completed {key} entries={result['entries']}", flush=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
