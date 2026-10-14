#!/usr/bin/env python3
"""Read-only Wyckoff event study on the training lake (never touches the holdout)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trader.lab.runner import INSTRUMENTS, holdout_months, load_histdata_candles
from trader.lab.wyckoff_events import summarise_events, volume_report, wyckoff_events


def _markdown(report: dict[str, object]) -> str:
    lines = ["# Wyckoff event study", ""]
    for market, body in report.items():
        assert isinstance(body, dict)
        volume = body["volume"]
        lines += [
            f"## {market}",
            "",
            "Informative volume share of five-minute bars: "
            f"{volume['informative_volume_share']:.3f}",
            "",
            "| N | depth | side | stage | events | mean (ATR) | placebo | lift | years |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for row in body["summary"]["rows"]:
            lines.append(
                f"| {row['range_bars']} | {row['break_atr']} | {row['side']} | {row['stage']} | "
                f"{row['events']} | {row['mean_result_atr']} | {row['placebo_mean_result_atr']} | "
                f"{row['lift_atr']} | {row['years_agreeing']} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="What follows Wyckoff springs and upthrusts")
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--holdout", type=Path, default=Path("/workspace/data/holdout"))
    parser.add_argument(
        "--out", type=Path, default=Path("docs/evidence/round_d_wyckoff_events.json")
    )
    parser.add_argument("--instruments", nargs="+", default=list(INSTRUMENTS))
    args = parser.parse_args()
    sealed = holdout_months(args.holdout, args.instruments)
    report: dict[str, object] = {}
    for instrument in args.instruments:
        five = load_histdata_candles(args.lake, instrument, 5, exclude_months=sealed)
        report[instrument] = {
            "volume": volume_report(five),
            "summary": summarise_events(wyckoff_events(five)),
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    args.out.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
