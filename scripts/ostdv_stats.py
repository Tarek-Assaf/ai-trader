#!/usr/bin/env python3
"""Read-only OSTDV zone statistics on the training lake (never touches the holdout)."""

from __future__ import annotations

import argparse
import json
from datetime import time
from pathlib import Path

from trader.lab.ostdv_stats import ostdv_days, summarise
from trader.lab.runner import INSTRUMENTS, holdout_months, load_histdata_candles


def main() -> int:
    parser = argparse.ArgumentParser(description="Where do daily highs/lows form vs the Asia range")
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--holdout", type=Path, default=Path("/workspace/data/holdout"))
    parser.add_argument("--out", type=Path, default=Path("docs/evidence/round_c_ostdv_stats.json"))
    parser.add_argument("--instruments", nargs="+", default=list(INSTRUMENTS))
    parser.add_argument(
        "--windows",
        nargs="+",
        default=["18:45-22:03"],
        help="range windows in New York time, e.g. 18:45-22:03 20:00-00:00 (sensitivity check)",
    )
    args = parser.parse_args()
    # The lake still holds the sealed months; only their names are read here, never their data.
    sealed = holdout_months(args.holdout, args.instruments)
    report: dict[str, object] = {}
    for instrument in args.instruments:
        candles = load_histdata_candles(args.lake, instrument, 5, exclude_months=sealed)
        by_window: dict[str, object] = {}
        for text in args.windows:
            start_text, end_text = text.split("-")
            by_window[text] = summarise(
                ostdv_days(
                    candles,
                    window_start=time.fromisoformat(start_text),
                    window_end=time.fromisoformat(end_text),
                )
            )
        # Top level keeps the first window's summary (the owner's window by default).
        report[instrument] = {**by_window[args.windows[0]], "by_window": by_window}  # type: ignore[dict-item]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
