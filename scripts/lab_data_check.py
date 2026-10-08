#!/usr/bin/env python3
"""Report HistData lake coverage before a Strategy Lab run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trader.lab.runner import (
    INSTRUMENTS,
    coverage_report,
    prepare_holdout,
    strategy_lab_holdout_months,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Strategy Lab HistData coverage")
    parser.add_argument("--lake", type=Path, default=Path("data/lake"))
    parser.add_argument("--instruments", nargs="+", default=list(INSTRUMENTS))
    parser.add_argument("--min-months", type=int, default=24)
    parser.add_argument("--holdout", type=Path, default=Path("data/holdout"))
    parser.add_argument("--config", type=Path, default=Path("config/strategy_lab.yaml"))
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
    if args.prepare_holdout:
        copied = prepare_holdout(
            args.lake,
            args.holdout,
            instruments=args.instruments,
            months=holdout_months,
        )
        print(f"prepared holdout files: {len(copied)}")
    reports = [coverage_report(args.lake, instrument) for instrument in args.instruments]
    print(json.dumps([report.payload() for report in reports], indent=2))
    print("instrument first_bar last_bar months gaps qa")
    for report in reports:
        print(
            f"{report.instrument} {report.first_bar or '-'} {report.last_bar or '-'} "
            f"{report.months} {report.gaps} {','.join(report.qa_flags) or '-'}"
        )
    return (
        0 if all(report.months >= args.min_months and report.gaps == 0 for report in reports) else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
