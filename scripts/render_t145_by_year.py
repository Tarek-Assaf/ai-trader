#!/usr/bin/env python3
"""Render compact T-145 evidence from the completed Pod diagnostic."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "t145_by_year.json"
SUMMARY = ROOT / "docs" / "evidence" / "t145_by_year_summary.json"
TABLE = ROOT / "docs" / "evidence" / "t145_by_year_table.md"
PERIODS = ("2022", "2023", "2024", "2025", "2026_to_holdout")


def n(x: Any) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x):.3f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(x)


def metric(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "trades": stats.get("trades", 0),
        "net_points": stats.get("net_points", 0.0),
        "profit_factor": stats.get("profit_factor", 0.0),
        "max_drawdown_points": stats.get("max_drawdown_points", 0.0),
    }


def main() -> None:
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    group_summary: dict[str, Any] = {}
    for group, data in raw["groups"].items():
        candidates = data.get("candidates", {})
        results = data.get("results", {})
        group_summary[group] = {"candidates": len(candidates), "period_results": len(results)}
        for digest, candidate in sorted(candidates.items(), key=lambda item: int(item[1].get("rank", 0))):
            periods: dict[str, Any] = {}
            for period in PERIODS:
                key = f"{group}|{digest}|{period}"
                result = results[key]
                periods[period] = {
                    "original": {"zero_cost": metric(result["stats"]["zero_cost"]), "normal_cost": metric(result["stats"]["normal_cost"])},
                    "mirror": {"zero_cost": metric(result["mirror"]["zero_cost"]), "normal_cost": metric(result["mirror"]["normal_cost"])},
                    "candles": result.get("stats", {}).get("candles", 0),
                    "missing_data": result.get("missing_data", False),
                }
            rows.append({
                "group": group,
                "rank": candidate.get("rank"),
                "strategy_hash": digest,
                "source": candidate.get("source"),
                "source_name": candidate.get("source_name"),
                "rules": candidate.get("rules"),
                "rules_readable": candidate.get("rules_readable"),
                "mirror_rules_readable": candidate.get("mirror_rules_readable"),
                "selection": candidate.get("selection"),
                "five_year_wins_zero_cost": candidate.get("five_year_wins_zero_cost", 0),
                "five_year_wins_normal_cost": candidate.get("five_year_wins_normal_cost", 0),
                "periods": periods,
                "continuous_2023_to_today": {
                    "original": {"zero_cost": metric(candidate["continuous_2023_to_today"]["zero_cost"]), "normal_cost": metric(candidate["continuous_2023_to_today"]["normal_cost"])},
                    "mirror": {"zero_cost": metric(candidate["continuous_mirror_2023_to_today"]["zero_cost"]), "normal_cost": metric(candidate["continuous_mirror_2023_to_today"]["normal_cost"])},
                },
            })
    summary = {
        "task": raw["task"],
        "generated_at": raw["generated_at"],
        "diagnostic_zero_cost": raw["diagnostic_zero_cost"],
        "sealed_holdout_read": raw["sealed_holdout_read"],
        "gate1_evaluated": raw["gate1_evaluated"],
        "broker_calls": raw["broker_calls"],
        "orders": raw["orders"],
        "periods": raw["periods"],
        "preflight": raw["preflight"],
        "group_summary": group_summary,
        "candidate_count": len(rows),
        "rows": rows,
        "comparison": raw["comparison"],
    }
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# T-145 by-year candidate table",
        "",
        "Diagnostic only. This run used zero-cost and pessimistic normal-cost models, mirrored every candidate direction, read no sealed holdout, placed no orders, and did not evaluate Gate 1.",
        "",
        f"Generated `{raw['generated_at']}`; periods end at the 2026-07-01 holdout boundary. Candidates: {len(rows)} (3 distinct per group).",
        "",
        "## Group summary",
        "",
        "| Group | Candidates | Period results |",
        "|---|---:|---:|",
    ]
    for group, info in group_summary.items():
        lines.append(f"| {group} | {info['candidates']} | {info['period_results']} |")
    lines += ["", "## Candidate metrics", "", "Each year cell is `trades / net-zero / net-normal / PF-zero / DD-zero`; wins are positive-net years across 2022–2026-to-holdout. Continuous cells use 2023-to-holdout and show original then mirrored net values. Rules are rendered in readable words from the reconstructed strategy definition.", "", "| Group | Rank | Strategy | Rules | 2022 | 2023 | 2024 | 2025 | 2026 | Wins z/n | Continuous original z/n | Mirror z/n |", "|---|---:|---|---|---|---|---|---|---|---:|---|---|"]
    for row in rows:
        cells = []
        for period in PERIODS:
            z = row["periods"][period]["original"]["zero_cost"]
            nn = row["periods"][period]["original"]["normal_cost"]
            cells.append(f"{z['trades']} / {n(z['net_points'])} / {n(nn['net_points'])} / {n(z['profit_factor'])} / {n(z['max_drawdown_points'])}")
        cont = row["continuous_2023_to_today"]
        orig = cont["original"]; mir = cont["mirror"]
        lines.append("| " + " | ".join([
            row["group"], str(row["rank"]), row["source_name"] or row["strategy_hash"][:12], row["rules_readable"] or "n/a", *cells,
            f"{row['five_year_wins_zero_cost']}/{row['five_year_wins_normal_cost']}",
            f"{n(orig['zero_cost']['net_points'])}/{n(orig['normal_cost']['net_points'])}",
            f"{n(mir['zero_cost']['net_points'])}/{n(mir['normal_cost']['net_points'])}",
        ]) + " |")
    lines += ["", "## NSXUSD 60m overnight/London comparison", "", "The exact historical 5-of-6 winner was not identifiable; this is the closest readable registered seed and is labelled as such in the raw evidence.", "", "| Period | Original net zero/normal | Mirror net zero/normal |", "|---|---:|---:|"]
    comp = raw["comparison"].get("NSXUSD:60_overnight_london_break", {})
    for period in PERIODS + ("2023_to_today",):
        value = comp.get(period)
        if value:
            lines.append(f"| {period} | {n(value['stats']['zero_cost']['net_points'])}/{n(value['stats']['normal_cost']['net_points'])} | {n(value['mirror']['zero_cost']['net_points'])}/{n(value['mirror']['normal_cost']['net_points'])} |")
    TABLE.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
