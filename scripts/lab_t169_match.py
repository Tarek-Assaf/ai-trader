#!/usr/bin/env python3
"""Match full-warm-up T-162 entries to the owner's 31 entries."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import lab_t162_pine_port as port


def condition_state(bars, features, index: int, direction: str) -> dict[str, bool]:
    bar = bars[index]
    ema, adx, plus, minus, vwap, atr = (
        features.ema[index],
        features.adx[index],
        features.plus_di[index],
        features.minus_di[index],
        features.vwap[index],
        features.atr[index],
    )
    direction_ok = features.buy_cross[index] if direction == "long" else features.sell_cross[index]
    ema_ok = ema is not None and (bar.close > ema if direction == "long" else bar.close < ema)
    adx_ok = (
        None not in (adx, plus, minus)
        and float(adx) >= 20
        and (float(plus) > float(minus) if direction == "long" else float(minus) > float(plus))
    )
    vwap_ok = (
        None not in (vwap, atr)
        and (bar.close > vwap if direction == "long" else bar.close < vwap)
        and abs(bar.close - float(vwap)) / float(atr) <= port.VWAP_DISTANCE_ATR
    )
    ha_open, ha_close = features.ha_open[index], features.ha_close[index]
    ha_ok = (
        ha_open is not None
        and ha_close is not None
        and (ha_close > ha_open if direction == "long" else ha_close < ha_open)
    )
    risk_ok = port._risk(bars, features, index, direction)[0]
    return {
        "direction": bool(direction_ok),
        "ema": bool(ema_ok),
        "adx_di": bool(adx_ok),
        "vwap": bool(vwap_ok),
        "ha_state": bool(ha_ok),
        "risk": bool(risk_ok),
        "no_chase": True,
    }


def nearest_trade(owner, trades):
    candidates = [trade for trade in trades if trade.direction == owner.direction]
    return min(
        candidates,
        key=lambda t: abs((t.entry_time - owner.entry_time).total_seconds()),
        default=None,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", type=Path, required=True)
    ap.add_argument("--owner-file", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    owners = port.read_owner_entries(args.owner_file)
    warm_start = min(o.entry_time for o in owners) - timedelta(days=7)
    end = datetime(2026, 10, 17, tzinfo=UTC)
    minutes = port.read_nas100_minutes(args.lake, warm_start, end)
    bars = tuple(item for item in port.resample_ohlc(minutes, port.TIMEFRAME) if item.complete)
    trades, features = port.replay(bars, "closed")
    exact = within = within2 = within3 = 0
    no_direction_within10 = 0
    histogram = Counter()
    price_diffs: list[float] = []
    rows = []
    unmatched = []
    last_exits = [trade.exit_index for trade in trades if trade.exit_index is not None]
    for owner in owners:
        nearest = nearest_trade(owner, trades)
        index, bar = port._bar_at(bars, owner.entry_time)
        offset = (
            None
            if nearest is None
            else (nearest.entry_time - owner.entry_time).total_seconds() / 180.0
        )
        if offset is not None and abs(offset) <= 10:
            histogram[str(int(round(offset)))] += 1
        if offset is None or abs(offset) > 10:
            no_direction_within10 += 1
        if offset is not None and abs(offset) <= 0.01:
            exact += 1
        if offset is not None and abs(offset) <= 1:
            within += 1
        if offset is not None and abs(offset) <= 2:
            within2 += 1
        if offset is not None and abs(offset) <= 3:
            within3 += 1
            price_diffs.append(nearest.entry_price - owner.entry_price)
        state = None
        if index is not None:
            state = condition_state(bars, features, index, owner.direction)
            cooldown = not any(
                exit_index is not None
                and exit_index < index
                and index - exit_index < port.REFRESH_BARS
                for exit_index in last_exits
            )
            state["cooldown"] = cooldown
            if offset is None or abs(offset) > 3:
                unmatched.append(
                    {
                        "owner_time": owner.entry_time.isoformat(),
                        "direction": owner.direction,
                        "conditions": state,
                    }
                )
        rows.append(
            {
                "owner_time": owner.entry_time.isoformat(),
                "direction": owner.direction,
                "owner_price": owner.entry_price,
                "nearest_port_time": nearest.entry_time.isoformat() if nearest else None,
                "nearest_port_price": nearest.entry_price if nearest else None,
                "offset_bars": offset,
                "within_3_bars": bool(offset is not None and abs(offset) <= 3),
                "conditions_at_owner_bar": state,
            }
        )
    payload = {
        "task": "T-169",
        "warmup_start": warm_start.isoformat(),
        "end": end.isoformat(),
        "bars": len(bars),
        "owner_entries": len(owners),
        "port_closed_trades": len(trades),
        "matches": {
            "exact": exact,
            "within_1": within,
            "within_2": within2,
            "within_3": within3,
            "owner_no_same_direction_within_10": no_direction_within10,
            "port_without_owner_within_3": sum(
                1
                for trade in trades
                if not any(
                    owner.direction == trade.direction
                    and abs((trade.entry_time - owner.entry_time).total_seconds()) <= 9 * 60
                    for owner in owners
                )
            ),
        },
        "offset_histogram_-10_to_10": {str(i): histogram.get(str(i), 0) for i in range(-10, 11)},
        "median_price_diff_within_3": median(price_diffs) if price_diffs else None,
        "matches_rows": rows,
        "unmatched_owner_diagnostics": unmatched[:10],
        "safety": {
            "broker_calls": 0,
            "orders": 0,
            "sealed_holdout_read": False,
            "lake_writes": 0,
            "production_state_writes": 0,
            "tuning": False,
        },
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    verdict = f"{within3} of {len(owners)} owner entries reproduced within +/-3 bars (target 25)."
    cause = (
        "The target is met; the historical rerun can go ahead with D-050 caveat."
        if within3 >= 25
        else (
            "The target is not met; remaining mismatches require owner-bar condition or "
            "price-basis review before rerun."
        )
    )
    lines = [
        "# T-169 owner entry match",
        "",
        f"**Verdict:** {verdict} {cause}",
        "",
        f"Closed-mode port trades: {len(trades)}; exact={exact}, +/-1={within}, "
        f"+/-2={within2}, +/-3={within3}; no same-direction entry within +/-10 "
        f"bars for {no_direction_within10} owner entries.",
        "",
        f"Median port-minus-owner entry price difference for +/-3-bar matches: "
        f"{median(price_diffs) if price_diffs else 'n/a'}.",
        "",
        "## Offset histogram (-10..+10 bars)",
        "",
        "```text",
        json.dumps(payload["offset_histogram_-10_to_10"], sort_keys=True),
        "```",
        "",
        "## Unmatched owner conditions",
        "",
        "The first unmatched owner rows and condition flags are in the JSON. The unchanged "
        "port has no separate no-chase predicate; it is recorded as true only to show it "
        "does not block these rows.",
        "",
        "Safety counters are all zero/false; no broker, order, holdout, lake-write, "
        "production-state, tuning or strategy-search activity.",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
