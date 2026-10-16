#!/usr/bin/env python3
"""Run the pre-registered T-145 by-year diagnostic with opposite mirrors."""
from __future__ import annotations

import argparse
import gc
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trader.lab.backtest import CostModel, run_backtest
from trader.lab.engine import IndicatorCache
from trader.lab.seeds import _london_break
from scripts.lab_recent_zero_cost import (
    INSTRUMENTS,
    _candidate_specs,
    _describe,
    _load_window,
    _trade_stats,
    _zero_cost,
    pessimistic_cost_model,
    spec_hash,
)

END_2026 = datetime(2026, 7, 1, tzinfo=UTC)
PERIODS = {
    "2022": (datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
    "2023": (datetime(2023, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
    "2024": (datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
    "2025": (datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
    "2026_to_holdout": (datetime(2026, 1, 1, tzinfo=UTC), END_2026),
    "2023_to_today": (datetime(2023, 1, 1, tzinfo=UTC), END_2026),
}
GROUPS = (
    ("NSXUSD", 1), ("NSXUSD", 3), ("NSXUSD", 5), ("NSXUSD", 60),
    ("XAUUSD", 1), ("XAUUSD", 3), ("XAUUSD", 5), ("XAUUSD", 60),
)
YEAR_KEYS = ("2022", "2023", "2024", "2025", "2026_to_holdout")


def stamp() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def rss_bytes() -> int | None:
    try:
        return int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def distinct_top(rows: list[dict[str, Any]], *, count: int = 3) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    hashes: set[str] = set()
    pairs: set[tuple[Any, Any]] = set()
    for row in rows:
        digest = str(row.get("strategy_hash", ""))
        stats = row.get("zero_cost", row)
        pair = (stats.get("trades"), stats.get("profit_factor"))
        if digest in hashes or pair in pairs:
            continue
        hashes.add(digest)
        pairs.add(pair)
        chosen.append(row)
        if len(chosen) >= count:
            break
    return chosen


def candidate_manifest(t144_path: Path, t143_path: Path) -> list[dict[str, Any]]:
    t144 = json.loads(t144_path.read_text(encoding="utf-8"))
    t143 = json.loads(t143_path.read_text(encoding="utf-8"))
    manifest: list[dict[str, Any]] = []
    for instrument, timeframe in GROUPS:
        group = f"{instrument}:{timeframe}"
        if group == "XAUUSD:1":
            rows = [
                row for row in t143["strategies"]
                if row.get("instrument") == instrument and int(row.get("timeframe", 0)) == timeframe
                and int(row.get("zero_cost", {}).get("trades", 0)) >= 10
            ]
            rows.sort(key=lambda row: float(row.get("zero_cost", {}).get("net_points", 0.0)), reverse=True)
            selected = distinct_top(rows)
            selection = "top 3 zero-cost net points among strategies with at least 10 trades"
        else:
            rows = list(t144["summary"].get(group, {}).get("top10", []))
            selected = distinct_top(rows)
            selection = "top 3 distinct entries from T-144 top-10 evidence"
        if len(selected) < 3:
            raise RuntimeError(f"only {len(selected)} candidates for {group}")
        for rank, row in enumerate(selected, 1):
            manifest.append({
                "group": group,
                "instrument": instrument,
                "timeframe": timeframe,
                "rank": rank,
                "selection": selection,
                "strategy_hash": row["strategy_hash"],
                "source": row.get("source"),
                "source_name": row.get("source_name"),
                "variant": row.get("variant"),
                "trades_in_selection_window": row.get("trades", row.get("zero_cost", {}).get("trades")),
                "profit_factor_in_selection_window": row.get("profit_factor", row.get("zero_cost", {}).get("profit_factor")),
                "net_points_zero_in_selection_window": row.get("net_points_zero", row.get("zero_cost", {}).get("net_points")),
                "rules": row.get("rules", ""),
            })
    return manifest


def pretty_number(value: Any) -> str:
    if value is None:
        return "n/a"
    if value == "inf":
        return "infinite"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.3f}".rstrip("0").rstrip(".")


def readable_rules(spec: Any) -> str:
    op = {"gt": "above", "gte": "at least", "lt": "below", "lte": "at most", "eq": "equal to"}
    indicator = {
        "flat_range_width_atr": "flat range width in ATR",
        "rolling_high": "rolling high",
        "rolling_low": "rolling low",
        "pre_trend_atr": "prior trend in ATR",
        "close": "close",
        "high": "high",
        "low": "low",
        "open": "open",
    }
    entries: list[str] = []
    for condition in spec.entry_conditions:
        left = indicator.get(getattr(condition, "indicator", "value"), getattr(condition, "indicator", "value"))
        operator = op.get(getattr(condition, "operator", ""), getattr(condition, "operator", ""))
        threshold_indicator = getattr(condition, "threshold_indicator", None)
        period = getattr(condition, "threshold_period", None)
        if threshold_indicator:
            right = indicator.get(threshold_indicator, threshold_indicator.replace("_", " "))
            if period:
                right += f" over {period} bars"
            text = f"{left} {operator} the {right}"
        else:
            threshold = getattr(condition, "threshold", "")
            name = getattr(threshold, "parameter", None)
            right = f"the {name.replace('_', ' ')} parameter" if name else pretty_number(threshold)
            text = f"{left} {operator} {right}"
        entries.append(text)
    exits: list[str] = []
    for rule in spec.exit_rules:
        kind = getattr(rule, "kind", "exit")
        if kind == "time_stop":
            exits.append(f"close after {getattr(rule, 'bars', '?')} bars")
        else:
            distance = pretty_number(getattr(rule, "distance", ""))
            unit = getattr(rule, "unit", "points")
            exits.append(f"{kind} at {distance} {unit}")
    sessions = ", ".join(getattr(spec, "session_filters", ())) or "any session"
    return f"{spec.direction} during {sessions}; entry when {' and '.join(entries)}; exits: {', '.join(exits)}"


def stats_pair(spec: Any, candles: tuple[Any, ...], instrument: str, timeframe: int, zero: CostModel, normal: CostModel) -> dict[str, Any]:
    if not candles:
        empty = {"trades": 0, "average_trade_points": 0.0, "best_trade_points": 0.0, "worst_trade_points": 0.0, "net_points": 0.0, "profit_factor": 0.0, "max_drawdown_points": 0.0}
        return {"zero_cost": empty, "normal_cost": dict(empty), "candles": 0}
    cache = IndicatorCache.from_candles(candles, timeframe)
    zr = run_backtest(spec, candles, cost_model=zero, indicator_cache=cache)
    nr = run_backtest(spec, candles, cost_model=normal, indicator_cache=cache)
    return {"zero_cost": _trade_stats(zr), "normal_cost": _trade_stats(nr), "candles": len(candles)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--config", type=Path, default=Path("config/strategy_lab.yaml"))
    parser.add_argument("--t144", type=Path, default=Path("/workspace/state/lab/t144_5m_3m_top10_summary.json"))
    parser.add_argument("--t143", type=Path, default=Path("/workspace/state/lab/t143_merged.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--memory-limit-gb", type=float, default=12.0)
    args = parser.parse_args()
    manifest = candidate_manifest(args.t144, args.t143)
    payload: dict[str, Any] = {
        "task": "T-145", "diagnostic_zero_cost": True, "generated_at": stamp(),
        "candidate_manifest": manifest, "periods": {k: [v[0].isoformat(), v[1].isoformat()] for k, v in PERIODS.items()},
        "sealed_holdout_read": False, "gate1_evaluated": False, "broker_calls": 0, "orders": 0,
        "preflight": {"cpu_count": os.cpu_count(), "memory_limit_gb": args.memory_limit_gb, "rss_start_bytes": rss_bytes()},
        "groups": {}, "comparison": {},
    }
    if args.checkpoint.exists():
        previous = json.loads(args.checkpoint.read_text(encoding="utf-8"))
        payload.update(previous)
        payload["candidate_manifest"] = manifest
    generated: dict[tuple[str, str], Any] = {}
    for instrument, timeframe in GROUPS:
        group = f"{instrument}:{timeframe}"
        rows = [row for row in manifest if row["group"] == group]
        all_specs = _candidate_specs(instrument, timeframe, staged_generations=3, max_staged_parents=10)
        by_hash = {spec_hash(row["spec"]): row["spec"] for row in all_specs}
        missing = [row["strategy_hash"] for row in rows if row["strategy_hash"] not in by_hash]
        if missing:
            raise RuntimeError(f"cannot rebuild candidate hashes for {group}: {missing}")
        payload["groups"].setdefault(group, {"instrument": instrument, "timeframe": timeframe, "candidates": {}})
        zero = _zero_cost(instrument)
        normal = pessimistic_cost_model(args.config)
        for period in PERIODS:
            start, end = PERIODS[period]
            candles = _load_window(args.lake, instrument, timeframe, start=start, end=end)
            for row in rows:
                key = f"{group}|{row['strategy_hash']}|{period}"
                if key in payload["groups"][group].get("results", {}):
                    continue
                spec = by_hash[row["strategy_hash"]]
                mirror = spec.model_copy(update={"direction": "long" if spec.direction == "short" else "short"})
                result = stats_pair(spec, candles, instrument, timeframe, zero, normal)
                mirror_result = stats_pair(mirror, candles, instrument, timeframe, zero, normal)
                payload["groups"][group].setdefault("results", {})[key] = {
                    "strategy_hash": row["strategy_hash"], "period": period, "stats": result,
                    "mirror_direction": mirror.direction, "mirror": mirror_result,
                    "missing_data": not bool(candles),
                }
                payload["groups"][group]["candidates"].setdefault(row["strategy_hash"], {
                    **row, "rules_readable": readable_rules(spec), "mirror_hash": spec_hash(mirror), "mirror_rules_readable": readable_rules(mirror)
                })
                write_json(args.checkpoint, payload)
                if rss_bytes() and rss_bytes() > args.memory_limit_gb * 1024**3:
                    raise RuntimeError(f"RSS exceeded limit: {rss_bytes()}")
            del candles
            gc.collect()
        print(f"completed {group} rss={rss_bytes()}", flush=True)
    # Derived five-year win counts and continuous totals.
    for group_data in payload["groups"].values():
        for digest, candidate in group_data.get("candidates", {}).items():
            per = {period: group_data["results"][f"{group_data['instrument']}:{group_data['timeframe']}|{digest}|{period}"] for period in PERIODS}
            candidate["five_year_wins_zero_cost"] = sum(float(per[p]["stats"]["zero_cost"]["net_points"]) > 0 for p in YEAR_KEYS)
            candidate["five_year_wins_normal_cost"] = sum(float(per[p]["stats"]["normal_cost"]["net_points"]) > 0 for p in YEAR_KEYS)
            candidate["continuous_2023_to_today"] = per["2023_to_today"]["stats"]
            candidate["continuous_mirror_2023_to_today"] = per["2023_to_today"]["mirror"]
    # Closest known overnight/London range-break comparison, pre-registered because exact historical winner is unavailable.
    comparison_spec = _london_break("NSXUSD", 60, direction="long", stop=1.5, target=3.0, with_trend=False)
    comp = {"label": "closest readable London break seed; exact historical 5-of-6 winner not identifiable", "strategy_hash": spec_hash(comparison_spec), "rules_readable": readable_rules(comparison_spec), "parameters": {"direction": "long", "stop": 1.5, "target": 3.0, "with_trend": False}}
    zero = _zero_cost("NSXUSD"); normal = pessimistic_cost_model(args.config)
    for period, (start, end) in PERIODS.items():
        candles = _load_window(args.lake, "NSXUSD", 60, start=start, end=end)
        mirror = comparison_spec.model_copy(update={"direction": "short"})
        comp[period] = {"stats": stats_pair(comparison_spec, candles, "NSXUSD", 60, zero, normal), "mirror_direction": mirror.direction, "mirror": stats_pair(mirror, candles, "NSXUSD", 60, zero, normal), "missing_data": not bool(candles)}
        del candles; gc.collect()
    payload["comparison"]["NSXUSD:60_overnight_london_break"] = comp
    payload["preflight"]["rss_end_bytes"] = rss_bytes()
    write_json(args.output, payload)
    write_json(args.checkpoint, payload)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
