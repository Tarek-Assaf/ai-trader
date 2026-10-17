#!/usr/bin/env python3
"""Merge isolated T-143 worker outputs without duplicate strategy rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.lab_recent_zero_cost import _group_summary, _stamp, _write_json


def _key(row: dict[str, Any]) -> tuple[str, int, str]:
    return (str(row["instrument"]), int(row["timeframe"]), str(row["strategy_hash"]))


def run(args: argparse.Namespace) -> int:
    sources = [Path(value) for value in args.source]
    rows_by_key: dict[tuple[str, int, str], dict[str, Any]] = {}
    metadata: dict[str, Any] = {}
    for source in sources:
        if not source.exists():
            continue
        payload = json.loads(source.read_text(encoding="utf-8"))
        metadata = {
            **metadata,
            **{k: v for k, v in payload.items() if k not in {"strategies", "summary"}},
        }
        for row in payload.get("strategies", []):
            rows_by_key.setdefault(_key(row), row)
    rows = list(rows_by_key.values())
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(f"{row['instrument']}:{row['timeframe']}", []).append(row)
    summaries = {
        group: _group_summary(group_rows, min_trades=args.min_trades)
        for group, group_rows in sorted(groups.items())
    }
    metadata.update(
        {
            "task": "T-143",
            "diagnostic_zero_cost": True,
            "worker_merge": True,
            "generated_at": _stamp(),
            "sealed_holdout_read": False,
            "gate1_evaluated": False,
            "broker_calls": 0,
            "orders": 0,
            "summary": summaries,
            "strategies": rows,
            "merged_sources": [str(path) for path in sources if path.exists()],
        }
    )
    _write_json(Path(args.output), metadata)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-trades", type=int, default=300)
    parser.add_argument("--source", nargs="+", required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
