#!/usr/bin/env python3
"""T-160 owner-time conversion and one bounded replay orchestration.

Only diagnostic owner-CSV readers use this conversion. Production timestamps,
the lake and strategy rules remain UTC. Ambiguous DST times use fold=0, the
earlier occurrence, and nonexistent local times are rejected by the explicit
round-trip check.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

OWNER_ZONE = ZoneInfo("America/New_York")


def parse_owner_datetime(value: str) -> datetime:
    """Parse a TradingView local timestamp and return an aware UTC datetime.

    ``fold=0`` selects the earlier occurrence for the DST fall-back hour. The
    owner export has minute precision, so this deterministic rule is recorded
    rather than guessing from market data.
    """

    local = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=OWNER_ZONE, fold=0)
    utc = local.astimezone(UTC)
    if utc.astimezone(OWNER_ZONE).replace(tzinfo=None) != local.replace(tzinfo=None):
        raise ValueError(f"nonexistent America/New_York local timestamp: {value}")
    return utc


def run(
    lake: Path, owner_file: Path, output: Path, replay: Path, report: Path
) -> dict[str, object]:
    """Rerun T-159 alignment and T-157 once with the shared conversion."""

    alignment = Path("docs/evidence/.t160_alignment.json")
    alignment_report = Path("docs/evidence/.t160_alignment.md")
    alignment.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "scripts/lab_t159_time_alignment.py",
            "--lake",
            str(lake),
            "--owner-file",
            str(owner_file),
            "--output",
            str(alignment),
            "--report",
            str(alignment_report),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/lab_t157_rule_by_rule.py",
            "--lake",
            str(lake),
            "--owner-file",
            str(owner_file),
            "--output",
            str(replay),
            "--report",
            str(report.with_suffix(".t157.md")),
        ],
        check=True,
    )
    payload = json.loads(alignment.read_text(encoding="utf-8"))
    replay_report = report.with_suffix(".t157.md").read_text(encoding="utf-8")
    center = re.search(r"baseline .* center-bar passes: \*\*(\d+/31)", replay_report)
    window = re.search(r"baseline passes somewhere .*: \*\*(\d+/31)", replay_report)
    payload["t157_replay"] = {
        "center_pass": center.group(1) if center else "unknown",
        "window_pass": window.group(1) if window else "unknown",
        "report_path": str(report.with_suffix(".t157.md")),
    }
    payload["owner_time_conversion"] = {
        "zone": "America/New_York",
        "known_answers": {
            "2026-10-06 10:00": "2026-10-06T14:00:00+00:00",
            "2026-12-01 10:00": "2026-12-01T15:00:00+00:00",
            "2026-11-01 01:30": "fold=0 (earlier occurrence), 2026-11-01T05:30:00+00:00",
        },
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    payload = run(args.lake, args.owner_file, args.output, args.replay, args.report)
    print(
        json.dumps(
            {
                "task": "T-160",
                "best_shift": payload["stage1_offset_confirmation"]["best_shift"],
                "t157": payload["t157_replay"],
                "safety": payload["safety"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
