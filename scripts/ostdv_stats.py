#!/usr/bin/env python3
"""Read-only OSTDV zone statistics on the training lake (never touches the holdout)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trader.lab.ostdv_stats import ostdv_days, summarise
from trader.lab.runner import INSTRUMENTS, load_histdata_candles


def main() -> int:
    parser = argparse.ArgumentParser(description="Where do daily highs/lows form vs the Asia range")
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--out", type=Path, default=Path("docs/evidence/round_c_ostdv_stats.json"))
    parser.add_argument("--instruments", nargs="+", default=list(INSTRUMENTS))
    args = parser.parse_args()
    report: dict[str, object] = {}
    for instrument in args.instruments:
        candles = load_histdata_candles(args.lake, instrument, 1)
        report[instrument] = summarise(ostdv_days(candles))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
