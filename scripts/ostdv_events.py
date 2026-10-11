#!/usr/bin/env python3
"""Read-only OSTDV level event study on the training lake (never touches the holdout)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trader.lab.ostdv_events import level_events, summarise_events
from trader.lab.runner import INSTRUMENTS, holdout_months, load_histdata_candles


def _markdown(report: dict[str, object]) -> str:
    lines = ["# OSTDV level event study", ""]
    for market, body in report.items():
        lines += [f"## {market}", ""]
        named = body["named"]  # type: ignore[index]
        placebo = body["placebo"]  # type: ignore[index]
        rates = [v["retrace_rate"] for v in placebo.values() if v["retrace_rate"] is not None]
        base = f"{sum(rates) / len(rates):.3f}" if rates else "n/a"
        lines += [f"Average retrace rate at placebo levels: {base}", ""]
        for name, level in named.items():
            if not name.endswith("/both"):
                continue
            lines.append(
                f"- **{name}**: {level['touches']} touches, retrace {level['retrace']}, "
                f"continue {level['continue']}, neither {level['neither']}, "
                f"retrace rate {level['retrace_rate']}"
            )
            for row in level["top_single_patterns"][:3]:
                lines.append(
                    f"  - {row['pattern']}: n={row['events']}, retrace rate "
                    f"{row['retrace_rate']} (lift {row['lift']}, years {row['years_agreeing']})"
                )
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="What repeats when price reaches an OSTDV level")
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--holdout", type=Path, default=Path("/workspace/data/holdout"))
    parser.add_argument("--out", type=Path, default=Path("docs/evidence/round_c_ostdv_events.json"))
    parser.add_argument("--instruments", nargs="+", default=list(INSTRUMENTS))
    args = parser.parse_args()
    # The lake still holds the sealed months; only their names are read here, never their data.
    sealed = holdout_months(args.holdout, args.instruments)
    report: dict[str, object] = {}
    for instrument in args.instruments:
        candles = load_histdata_candles(args.lake, instrument, 5, exclude_months=sealed)
        report[instrument] = summarise_events(level_events(candles))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    args.out.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
