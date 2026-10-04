#!/usr/bin/env python3
"""Import HistData M1 archives or text files into the local data lake."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from trader.data.importers.histdata import import_histdata
from trader.data.importers.histdata_download import download_histdata
from trader.data.importers.histdata_range import import_histdata_range
from trader.data.importers.histdata_ticks import rebuild_histdata_ticks


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download or read HistData data and write bid M1 Parquet plus QA JSON."
    )
    parser.add_argument("source", nargs="?", type=Path, help="Local ZIP, CSV/TXT, or directory")
    parser.add_argument("--symbol", required=True, choices=("NSXUSD", "XAUUSD"))
    parser.add_argument("--download", action="store_true", help="Download a completed month")
    parser.add_argument("--year", type=int)
    parser.add_argument("--from-year", type=int, help="First year in an inclusive batch range")
    parser.add_argument("--through-year", type=int, help="Last year in an inclusive batch range")
    parser.add_argument("--pause-seconds", type=float, default=1.0)
    parser.add_argument(
        "--dry-run", action="store_true", help="list available archive periods without importing"
    )
    parser.add_argument("--manifest", type=Path, help="SHA-256 manifest path for batch imports")
    parser.add_argument("--month", type=int)
    parser.add_argument("--raw", type=Path, default=Path("data/raw/histdata"))
    parser.add_argument(
        "--from-ticks", action="store_true", help="Rebuild bid M1 from timestamp-ordered raw quotes"
    )
    parser.add_argument("--lake", type=Path, default=Path("data/lake"))
    parser.add_argument("--lineage", type=Path, default=Path("data_lineage"))
    args = parser.parse_args()
    if args.from_year is not None or args.through_year is not None:
        if (
            args.from_year is None
            or args.through_year is None
            or args.source is not None
            or args.download
            or args.year is not None
            or args.month is not None
            or args.from_ticks
        ):
            parser.error(
                "batch mode needs --from-year and --through-year; omit source, --download, "
                "--year, --month and --from-ticks"
            )
        try:
            summary = import_histdata_range(
                args.symbol,
                args.from_year,
                args.through_year,
                args.raw,
                args.lake,
                args.lineage,
                args.manifest,
                args.pause_seconds,
                dry_run=args.dry_run,
            )
        except (ValueError, OSError):
            print(json.dumps({"status": "error", "reason": "HistData range import failed"}))
            return 1
        print(json.dumps(summary, indent=2, default=str))
        return 0 if summary["status"] in {"ok", "planned"} else 1

    if args.dry_run:
        parser.error("--dry-run is available only with --from-year and --through-year")

    if args.download:
        if args.source or args.year is None or args.month is None:
            parser.error("--download needs --year and --month, with no local source")
        try:
            source = download_histdata(
                args.symbol, args.year, args.month, args.raw, "T" if args.from_ticks else "M1"
            )
        except (ValueError, OSError):
            print(json.dumps({"status": "error", "reason": "HistData download failed"}))
            return 1
    else:
        if args.source is None or args.year is not None or args.month is not None:
            parser.error("provide a local source, or use --download --year --month")
        source = args.source
    try:
        reports = (
            rebuild_histdata_ticks(source, args.symbol, args.lake, args.lineage)
            if args.from_ticks
            else import_histdata(source, args.symbol, args.lake, args.lineage)
        )
    except (ValueError, OSError, zipfile.BadZipFile, KeyError):
        print(json.dumps({"status": "error", "reason": "HistData source validation failed"}))
        return 1
    # Reports contain source names and QA metadata only, never credentials.
    print(json.dumps(reports, indent=2, default=str))
    return 1 if not reports or any(report["status"] != "ok" for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
