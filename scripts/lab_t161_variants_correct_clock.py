#!/usr/bin/env python3
"""Rerun the registered Tai switches with the owner's New York clock.

This is a bounded, read-only diagnostic.  It reuses the exact T-157 switch
set and T-158 Heikin-Ashi categories once after T-160's owner-time fix.  It
does not call a broker, read the sealed holdout, tune parameters, or write
the lake, gate, ledger, promotion or production state.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from trader.data.resample import Candle, resample_ohlc
except ModuleNotFoundError:  # direct execution from a checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from trader.data.resample import Candle, resample_ohlc

try:
    from scripts.lab_t151_owner_trades import (
        OwnerEntry,
        aggregate_3m,
        read_nas100_minutes,
        read_owner_entries,
    )
    from scripts.lab_t156_feature_dump import _read_side_minutes
    from scripts.lab_t157_rule_by_rule import (
        OFFSETS,
        RULES,
        VARIANTS,
        _rows_for_basis,
    )
    from scripts.lab_t158_pipeline_audit import _read_window
    from scripts.lab_tai_smart_filter import heikin_ashi
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lab_t151_owner_trades import (
        OwnerEntry,
        aggregate_3m,
        read_nas100_minutes,
        read_owner_entries,
    )
    from lab_t156_feature_dump import _read_side_minutes
    from lab_t157_rule_by_rule import OFFSETS, RULES, VARIANTS, _rows_for_basis
    from lab_t158_pipeline_audit import _read_window
    from lab_tai_smart_filter import heikin_ashi


TIMEFRAMES = (1, 3, 5, 54, 60)
INSTRUMENTS = ("NSXUSD", "XAUUSD")


def _colour(bar: Candle | None) -> str:
    if bar is None:
        return "missing"
    if bar.close > bar.open:
        return "long"
    if bar.close < bar.open:
        return "short"
    return "neutral"


def _owner_verdict(variants: dict[str, Any], entries: int) -> dict[str, Any]:
    best_name, best = max(
        variants.items(),
        key=lambda item: (int(item[1].get("window_pass", 0)), -list(variants).index(item[0])),
        default=("none", {"window_pass": 0, "center_pass": 0}),
    )
    return {
        "best_switch": best_name,
        "best_center_pass": int(best.get("center_pass", 0)),
        "best_window_pass": int(best.get("window_pass", 0)),
        "entries": entries,
        "reaches_twenty": int(best.get("window_pass", 0)) >= 20,
    }


def _stage4_ha(
    lake: Path, entries: Sequence[OwnerEntry], start: datetime, end: datetime
) -> dict[str, Any]:
    """Return the T-158 HA categories and the requested first-five snapshots."""
    minutes = _read_instrument_minutes(lake, "NSXUSD", start, end)
    bars54 = tuple(item for item in resample_ohlc(minutes, 54) if item.complete)
    ha = heikin_ashi(bars54)
    categories = {
        "flip_current_bar": 0,
        "same_colour_no_flip": 0,
        "opposite_colour": 0,
        "missing": 0,
    }
    entry_rows: list[dict[str, Any]] = []
    for entry in entries:
        current = max(
            (item for item in ha if item.ts_utc <= entry.entry_time),
            key=lambda item: item.ts_utc,
            default=None,
        )
        current_colour = _colour(current)
        previous = max(
            (item for item in ha if current and item.ts_utc < current.ts_utc),
            key=lambda item: item.ts_utc,
            default=None,
        )
        previous_colour = _colour(previous)
        if current is None:
            categories["missing"] += 1
        elif current_colour == entry.direction and previous_colour != entry.direction:
            categories["flip_current_bar"] += 1
        elif current_colour == entry.direction:
            categories["same_colour_no_flip"] += 1
        else:
            categories["opposite_colour"] += 1
        if len(entry_rows) < 5:
            prior = [item for item in ha if item.ts_utc <= entry.entry_time][-8:]
            recorded = next((item for item in minutes if item.ts_utc == entry.entry_time), None)
            entry_rows.append(
                {
                    "entry": entry.entry_time.isoformat(),
                    "direction": entry.direction,
                    "owner_entry_price": entry.entry_price,
                    "recorded_nas100_mid_close": None if recorded is None else recorded.close,
                    "price_difference_owner_minus_recorded": None
                    if recorded is None
                    else entry.entry_price - recorded.close,
                    "ha_candles_before_entry": [
                        {
                            "ts": item.ts_utc.isoformat(),
                            "open": item.open,
                            "close": item.close,
                            "colour": _colour(item),
                        }
                        for item in prior
                    ],
                }
            )
    return {
        "entries": len(entries),
        "categories": categories,
        "first_five_entry_snapshots": entry_rows,
        "source": (
            "NSXUSD 1m lake, resampled to complete 54m bars; owner times parsed as "
            "America/New_York"
        ),
    }


def _coverage(lake: Path, start: datetime, end: datetime) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for instrument in INSTRUMENTS:
        minutes = _read_instrument_minutes(lake, instrument, start, end)
        result[instrument] = {
            "source_1m_rows": len(minutes),
            "timeframes": {
                str(timeframe): {
                    "bars": sum(item.complete for item in resample_ohlc(minutes, timeframe)),
                    "complete": sum(item.complete for item in resample_ohlc(minutes, timeframe)),
                }
                for timeframe in TIMEFRAMES
            },
        }
    return result


def _read_instrument_minutes(
    lake: Path, instrument: str, start: datetime, end: datetime
) -> tuple[Candle, ...]:
    """Read the canonical symbol, retaining the NAS100/NSXUSD lake alias."""
    if instrument == "NSXUSD":
        return read_nas100_minutes(lake, start, end)
    return _read_window(lake, instrument, start, end)


def run(lake: Path, owner_file: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entries = read_owner_entries(owner_file)
    if len(entries) != 31:
        raise RuntimeError(f"expected 31 owner positions, found {len(entries)}")
    start = min(entry.entry_time for entry in entries) - timedelta(days=2)
    end = max(entry.entry_time for entry in entries) + timedelta(minutes=15)

    minutes_by_basis: dict[str, Sequence[Candle]] = {"mid": read_nas100_minutes(lake, start, end)}
    for basis in ("bid", "ask"):
        side_minutes = _read_side_minutes(lake, start, end, basis)
        if side_minutes:
            minutes_by_basis[basis] = side_minutes
    rows: list[dict[str, Any]] = []
    basis_summaries: dict[str, Any] = {}
    for basis, minutes in minutes_by_basis.items():
        chart = aggregate_3m(minutes)
        basis_rows, summaries = _rows_for_basis(entries, tuple(chart), basis)
        rows.extend(basis_rows)
        basis_summaries[basis] = {
            "variants": summaries,
            "source_minutes": len(minutes),
            "chart_bars": len(chart),
        }
    if not rows:
        raise RuntimeError("NAS100 1m Capital.com data is missing")
    mid_variants = basis_summaries["mid"]["variants"]
    payload: dict[str, Any] = {
        "task": "T-161",
        "generated_at": datetime.now(UTC).isoformat(),
        "owner_timezone": "America/New_York",
        "owner_entries": len(entries),
        "registered_variants": list(VARIANTS),
        "offsets_bars": list(OFFSETS),
        "rule_order": list(RULES),
        "bases": basis_summaries,
        "owner_verdict": _owner_verdict(mid_variants, len(entries)),
        "coverage": _coverage(lake, start, end),
        "stage4_ha": _stage4_ha(lake, entries, start, end),
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "sealed_holdout_read": False,
            "gate_evaluated": False,
            "cost_ledger_promotion_writes": 0,
            "lake_writes": 0,
            "production_state_writes": 0,
        },
    }
    return payload, rows


def _report(payload: dict[str, Any]) -> str:
    verdict = payload["owner_verdict"]
    best = verdict["best_switch"]
    best_count = verdict["best_window_pass"]
    lines = [
        "# T-161 corrected-clock variant rerun",
        "",
        f"**Owner verdict:** The best registered switch is `{best}`, passing "
        f"**{best_count}/{verdict['entries']}** entries somewhere within ±3 "
        f"three-minute bars. It does "
        f"{'reach' if verdict['reaches_twenty'] else 'not reach'} the 20/31 "
        "stop threshold; no new switches or tuning were attempted.",
        "",
        f"Generated: `{payload['generated_at']}`. Owner timestamps were parsed as "
        "`America/New_York` and converted to UTC in comparison readers only.",
        "",
        "## Registered switch counts (mid price)",
        "",
        "| Variant | Center all-pass | Any pass in ±3 bars |",
        "| --- | ---: | ---: |",
    ]
    for name, summary in payload["bases"]["mid"]["variants"].items():
        lines.append(f"| `{name}` | {summary['center_pass']} | {summary['window_pass']} |")
    lines += [
        "",
        "## Price bases",
        "",
        "| Basis | Center all-pass | Any pass in ±3 bars |",
        "| --- | ---: | ---: |",
    ]
    baseline = "forming_wilder_sma_ema_utc"
    for basis, summary in payload["bases"].items():
        item = summary["variants"][baseline]
        lines.append(f"| {basis} | {item['center_pass']} | {item['window_pass']} |")
    lines += ["", "## First-failure counts for the baseline", ""]
    for name, count in payload["bases"]["mid"]["variants"][baseline]["first_failed_rule"].items():
        lines.append(f"- `{name}`: {count}")
    categories = payload["stage4_ha"]["categories"]
    lines += [
        "",
        "## T-158 Heikin-Ashi categories",
        "",
        json.dumps(categories, indent=2, sort_keys=True),
        "",
    ]
    lines += [
        "The report includes the last eight complete 54m HA candles before each of the "
        "first five owner entries, plus the owner's NQ1 price and the recorded NAS100 "
        "mid close at that minute.",
        "",
        "## Coverage and safety",
        "",
        "The bounded lake check covers NSXUSD and XAUUSD at 1m, 3m, 5m, 54m and 60m "
        "where data is present; 1m is mandatory.",
        "",
        f"The Pod produced {payload['full_replay_rows']} replay rows. The committed CSV is a "
        f"compact center-bar baseline index ({payload['compact_replay_rows']} rows); the "
        f"full artifact SHA-256 is `{payload['full_replay_sha256']}`.",
        "",
        json.dumps(payload["safety"], indent=2, sort_keys=True),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--owner-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/t161_variants.json"))
    parser.add_argument("--replay", type=Path, default=Path("docs/evidence/t161_replay.csv"))
    parser.add_argument("--report", type=Path, default=Path("docs/evidence/t161_report.md"))
    args = parser.parse_args()
    payload, rows = run(args.lake, args.owner_file)
    payload["full_replay_rows"] = len(rows)
    full_csv = ""
    if rows:
        full_fields: list[str] = []
        for row in rows:
            full_fields.extend(name for name in row if name not in full_fields)
        from io import StringIO

        full_buffer = StringIO()
        full_writer = csv.DictWriter(full_buffer, fieldnames=full_fields)
        full_writer.writeheader()
        full_writer.writerows(rows)
        full_csv = full_buffer.getvalue()
    payload["full_replay_sha256"] = hashlib.sha256(full_csv.encode()).hexdigest()
    compact_rows = [
        row
        for row in rows
        if row.get("basis") == "mid"
        and row.get("variant") == "forming_wilder_sma_ema_utc"
        and row.get("offset") == 0
    ]
    payload["compact_replay_rows"] = len(compact_rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.replay.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in compact_rows:
        fieldnames.extend(name for name in row if name not in fieldnames)
    with args.replay.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(compact_rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_report(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "task": "T-161",
                "owner_verdict": payload["owner_verdict"],
                "safety": payload["safety"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
