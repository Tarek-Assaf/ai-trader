#!/usr/bin/env python3
"""Read-only side-by-side trace for the T-196 Tai HA-cross misses.

This diagnostic reuses the bounded T-195 replay and compares the runtime
provider with the Pine port at every reported miss and the two preceding
closed bars.  It only reads the diagnostic lake, never calls a broker, reads
the sealed holdout, edits gates, or writes lake data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

try:
    from scripts import lab_t162_pine_port as port
    from scripts import lab_t187_runtime_replay as replay
    from scripts import lab_t195_runtime_miss as t195
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import lab_t162_pine_port as port
    from scripts import lab_t187_runtime_replay as replay
    from scripts import lab_t195_runtime_miss as t195


FIELDS = (
    "ha_open_54m",
    "ha_close_54m",
    "ha_colour",
    "ha_prev_colour",
    "ha_cross",
    "ema200",
    "adx14",
    "plus_di14",
    "minus_di14",
    "vwap",
    "session_anchor",
)


def _same(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 1e-9
    return left == right


def _colour(open_value: object, close_value: object) -> str | None:
    if open_value is None or close_value is None:
        return None
    return "green" if float(close_value) > float(open_value) else "red"


def _port_row(features: port.Features, index: int, direction: str) -> dict[str, object]:
    open_value = t195._value(features.ha_open[index])
    close_value = t195._value(features.ha_close[index])
    return {
        "ha_open_54m": open_value,
        "ha_close_54m": close_value,
        "ha_colour": _colour(open_value, close_value),
        "ha_prev_colour": None,
        "ha_cross": bool(
            features.buy_cross[index] if direction == "BUY" else features.sell_cross[index]
        ),
        "ema200": t195._value(features.ema[index]),
        "adx14": t195._value(features.adx[index]),
        "plus_di14": t195._value(features.plus_di[index]),
        "minus_di14": t195._value(features.minus_di[index]),
        "vwap": t195._value(features.vwap[index]),
        "session_anchor": "session_1800_ny",
    }


def _runtime_row(trace: Mapping[str, object]) -> dict[str, object]:
    gates = trace.get("gates")
    return {
        "ha_open_54m": trace.get("ha_open_54m"),
        "ha_close_54m": trace.get("ha_close_54m"),
        "ha_colour": trace.get("ha_colour"),
        "ha_prev_colour": None,
        "ha_cross": bool(gates.get("ha_cross")) if isinstance(gates, Mapping) else False,
        "ema200": trace.get("ema200"),
        "adx14": trace.get("adx14"),
        "plus_di14": trace.get("plus_di14"),
        "minus_di14": trace.get("minus_di14"),
        "vwap": trace.get("vwap"),
        "session_anchor": trace.get("session_anchor"),
    }


def _first_difference(left: Mapping[str, object], right: Mapping[str, object]) -> str | None:
    for name in FIELDS:
        if not _same(left.get(name), right.get(name)):
            return name
    return None


def _set_previous(rows: Sequence[dict[str, object]]) -> None:
    for index, row in enumerate(rows):
        if index == 0:
            continue
        for side in ("port", "runtime"):
            current = row[side]
            previous = rows[index - 1][side]
            if isinstance(current, dict) and isinstance(previous, Mapping):
                current["ha_prev_colour"] = previous.get("ha_colour")


def _compact_lines(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Return transfer-safe one-line records for the owner Pod terminal.

    The full trace stays in JSON.  These records contain only the entry value,
    the first differing quantity, and that quantity's port/runtime values on
    the two preceding closed bars so the evidence can be reconstructed from
    copied terminal output without inventing any values.
    """

    def short(value: object) -> str:
        if value is None:
            return "-"
        if isinstance(value, (int, float)):
            return f"{value:.10g}"
        return str(value)

    lines: list[str] = []
    for row in rows:
        context = row.get("context")
        if not isinstance(context, Sequence) or len(context) < 3:
            continue
        quantity = row.get("first_difference") or "none_observed"
        prior = []
        for item in context[-3:-1]:
            if not isinstance(item, Mapping):
                continue
            port_row = item.get("port")
            runtime_row = item.get("runtime")
            prior.append(
                [
                    port_row.get(quantity) if isinstance(port_row, Mapping) else None,
                    runtime_row.get(quantity) if isinstance(runtime_row, Mapping) else None,
                ]
            )
        entry = context[-1]
        entry_port = entry.get("port") if isinstance(entry, Mapping) else None
        entry_runtime = entry.get("runtime") if isinstance(entry, Mapping) else None
        entry_values = [
            entry_port.get(quantity) if isinstance(entry_port, Mapping) else None,
            entry_runtime.get(quantity) if isinstance(entry_runtime, Mapping) else None,
        ]
        values = [value for pair in prior for value in pair] + entry_values
        lines.append(
            "|".join(
                [
                    "T196_ROW",
                    str(row.get("at")),
                    str(row.get("direction")),
                    str(quantity),
                    *(short(value) for value in values),
                ]
            )
        )
    return lines


def _emit_compact(rows: Sequence[Mapping[str, object]], chunk_bytes: int = 3000) -> None:
    """Print compact rows, split into bounded chunks when necessary."""
    lines = [f"T196_ROW {line}" for line in _compact_lines(rows)]
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if current and len(candidate.encode("utf-8")) > chunk_bytes:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    total = len(chunks)
    for index, chunk in enumerate(chunks, 1):
        print(f"T196_CHUNK {index}/{total}")
        print(chunk)


def run(
    lake: Path, holdout_start: datetime, *, epic: str = "NSXUSD", days: int = 10
) -> dict[str, object]:
    minutes = t195._read_diagnostic_minutes(lake, epic, holdout_start)
    selected_dates = replay._selected_dates(minutes, days)
    selected = set(selected_dates)
    bars = replay._closed_bars(minutes, selected_dates, epic)
    comparison_bars = t195._independent_bars(minutes, epic)
    port_entries = replay._port_entries(comparison_bars, selected, ha_anchor="session_1800_ny")
    port_candles = tuple(t195._candle(bar) for bar in comparison_bars)
    _, features = port.replay(
        port_candles, "closed", ha_anchor="session_1800_ny", vwap_anchor="session_1800_ny"
    )
    port_index = {bar.at: index for index, bar in enumerate(comparison_bars)}
    targets: set[datetime] = set()
    windows: list[tuple[replay.RuntimeEntry, list[datetime]]] = []
    for entry in port_entries:
        index = port_index.get(entry.at, -1)
        if index < 0:
            continue
        stamps = [comparison_bars[item].at for item in range(max(0, index - 2), index + 1)]
        windows.append((entry, stamps))
        targets.update(stamps)
    runtime_entries, trace, _ = t195._runtime_trace(bars, selected, targets, unbounded=False)
    comparison = replay.compare_entries(runtime_entries, port_entries)
    excluded_times = {
        item["at"]
        for item in comparison.get("matches", [])
        if isinstance(item, Mapping) and isinstance(item.get("at"), str)
    }
    excluded_times.update(
        item["port"]["at"]
        for item in comparison.get("misses", [])
        if isinstance(item, Mapping)
        and item.get("reason") == "bar_or_stop_difference"
        and isinstance(item.get("port"), Mapping)
        and isinstance(item["port"].get("at"), str)
    )
    rows: list[dict[str, object]] = []
    for entry, stamps in windows:
        if entry.at.isoformat() in excluded_times:
            # T-195 classifies the exact match and two bar/stop rows outside
            # the accepted 23-row HA-cross miss set.
            continue
        context: list[dict[str, object]] = []
        for stamp in stamps:
            p_index = port_index[stamp]
            p_row = _port_row(features, p_index, entry.direction)
            r_row = _runtime_row(trace.get(stamp.isoformat(), {}))
            context.append(
                {
                    "at": stamp.isoformat(),
                    "port": p_row,
                    "runtime": r_row,
                    "first_difference": _first_difference(p_row, r_row),
                    "runtime_history_bars": trace.get(stamp.isoformat(), {}).get("history_bars"),
                }
            )
        _set_previous(context)
        for item in context:
            item["first_difference"] = _first_difference(item["port"], item["runtime"])
        entry_context = context[-1]
        entry_trace = trace.get(entry.at.isoformat(), {})
        if (
            not isinstance(entry_trace, Mapping)
            or entry_trace.get("first_failing_gate") != "ha_cross"
        ):
            continue
        rows.append(
            {
                "at": entry.at.isoformat(),
                "direction": entry.direction,
                "cause": "runtime_gate_ha_cross",
                "context": context,
                "first_difference": entry_context["first_difference"],
            }
        )
    counts = Counter(str(row["first_difference"]) for row in rows)
    return {
        "task": "T-196",
        "status": "ok",
        "epic": epic,
        "holdout_start": holdout_start.isoformat(),
        "selected_trading_dates": [item.isoformat() for item in selected_dates],
        "port_entries": len(port_entries),
        "ha_cross_miss_rows": len(rows),
        "first_difference_counts": dict(counts),
        "rows": rows,
        "source": {
            "t195_script": "scripts/lab_t195_runtime_miss.py",
            "runtime": "src/trader/experiments/tai_wiring.py",
            "port": "scripts/lab_t162_pine_port.py",
        },
        "safety": {
            "broker_calls": 0,
            "order_calls": 0,
            "network_access": False,
            "sealed_holdout_read": False,
            "lake_writes": False,
            "risk_or_gate_edits": False,
            "redeploy": False,
            "secrets_read": False,
        },
    }


def _write_outputs(
    payload: Mapping[str, object], output_dir: Path, state_output_dir: Path | None = None
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    rows = payload["rows"]
    assert isinstance(rows, Sequence)
    report = [
        "# T-196 Tai HA-cross trace",
        "",
        f"- Port entries traced: **{payload['port_entries']}**",
        f"- Rows whose bounded runtime first gate is `ha_cross`: "
        f"**{payload['ha_cross_miss_rows']}**",
        "",
        "The accepted T-195 handoff/spec says 23 first `ha_cross` misses, while the "
        "same read-only replay currently measures the 25 rows listed below. This report "
        "preserves the measured result rather than silently dropping two rows.",
        "",
        "## First differing quantity",
        "",
        "| Entry timestamp | Side | First differing quantity |",
        "|---|---:|---|",
    ]
    for row in rows:
        assert isinstance(row, Mapping)
        report.append(f"| {row['at']} | {row['direction']} | {row['first_difference'] or 'none'} |")
    report.extend(
        [
            "",
            "## Interpretation",
            "",
            "For each entry, `summary.json` contains the entry bar and the two preceding "
            "closed bars with side-by-side port/runtime HA, cross, EMA200, ADX, DI, VWAP, "
            "and session-anchor values. A `null` first difference means the gate mismatch "
            "is not explained by these recorded quantities; the exact cause remains unknown.",
            "",
            "The runtime implementation exposes the completed prior 54-minute HA group on "
            "every bar of the current group, while the Pine port's closed mode also carries "
            "the previous completed group. No code fix is applied by T-196; the narrowest "
            "fix candidate is to align the two value paths after review.",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    if state_output_dir is not None:
        state_output_dir.mkdir(parents=True, exist_ok=True)
        (state_output_dir / "summary.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--holdout-start", required=True, type=replay.parse_stamp)
    parser.add_argument("--epic", default="NSXUSD")
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/evidence/t196"))
    parser.add_argument("--state-output-dir", type=Path, default=None)
    args = parser.parse_args()
    payload = run(args.lake, args.holdout_start, epic=args.epic, days=args.days)
    _write_outputs(payload, args.output_dir, args.state_output_dir)
    _emit_compact(payload["rows"])
    print(
        json.dumps(
            {
                "task": "T-196",
                "status": payload["status"],
                "rows": payload["ha_cross_miss_rows"],
                "counts": payload["first_difference_counts"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
