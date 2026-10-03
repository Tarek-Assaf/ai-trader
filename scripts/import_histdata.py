#!/usr/bin/env python3
"""Import a HistData monthly ZIP or text file into the local data lake."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trader.data.importers.histdata import import_histdata


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read a local HistData ZIP/CSV/TXT file and write bid M1 Parquet plus QA JSON."
    )
    parser.add_argument("source", type=Path, help="Downloaded ZIP, CSV/TXT, or directory")
    parser.add_argument("--symbol", required=True, choices=("NSXUSD", "XAUUSD"))
    parser.add_argument("--lake", type=Path, default=Path("data/lake"))
    parser.add_argument("--lineage", type=Path, default=Path("data_lineage"))
    args = parser.parse_args()
    reports = import_histdata(args.source, args.symbol, args.lake, args.lineage)
    # Reports contain source names and QA metadata only, never credentials.
    print(json.dumps(reports, indent=2, default=str))
    return 1 if any(report["status"] == "error" for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())\n
