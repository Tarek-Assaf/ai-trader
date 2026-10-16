"""Reference statistics for the T-146 movement-matching study (pure functions).

Written by Claude as the reference for V-379/V-381. Codex wires these into
``lab_t146_matching.py`` without changing the formulas. No I/O, no broker,
no config, no holdout access.

Pre-registered rules
--------------------
* Direction of a trade = sign of the MEDIAN forward move (points) of the
  past matches (query level) or of the in-sample cluster members (cluster
  level). Median 0 or no data = flat = no trade.
* Net of one trade = direction * realized_move_points - cost_points. The cost
  is ALWAYS subtracted. A flat direction has no trade and no net.
* Out-of-sample unit = one QUERY. ``realized`` is the query's OWN forward move.
  The match prediction and the random-control prediction are both applied to
  that same realized move, so they are compared per query (paired).
* Edge = mean over queries of (net_matches - net_control), where a query with
  a flat direction contributes net 0 (no trade) on that side.
* p-value = exact two-sided sign test on the non-zero paired differences.
* FOUND only if: trades >= MIN_TRADES, mean net of the match trades > 0,
  edge > 0, and p <= 0.05 / trial_count (Bonferroni on the FULL trial count:
  every symbol, timeframe, length, method, horizon, cluster k counts).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

MIN_TRADES = 30
ALPHA = 0.05


def direction_from_moves(moves: Sequence[float | None]) -> int:
    """+1 / -1 / 0 from the sign of the median of the available moves."""
    values = [float(move) for move in moves if move is not None]
    if not values:
        return 0
    mid = median(values)
    return 1 if mid > 0 else -1 if mid < 0 else 0


def trade_net(direction: int, realized_points: float | None, cost_points: float) -> float | None:
    """Net points of one trade; None when there is no trade or no outcome."""
    if direction == 0 or realized_points is None:
        return None
    return direction * float(realized_points) - float(cost_points)


def sign_test_pvalue(differences: Sequence[float]) -> float | None:
    """Exact two-sided sign test on non-zero differences."""
    positive = sum(value > 0 for value in differences)
    negative = sum(value < 0 for value in differences)
    trials = positive + negative
    if trials == 0:
        return None
    tail = sum(math.comb(trials, i) for i in range(min(positive, negative) + 1)) / 2**trials
    return min(1.0, 2.0 * tail)


@dataclass(frozen=True, slots=True)
class QueryRecord:
    """One out-of-sample query at one horizon.

    ``match_moves`` / ``control_moves``: forward moves (points) of the past
    matches / of the random control windows. ``realized``: the query's own
    forward move (points), None when not available.
    """

    match_moves: tuple[float | None, ...]
    control_moves: tuple[float | None, ...]
    realized: float | None


def evaluate_group(
    records: Sequence[QueryRecord], *, cost_points: float, trial_count: int
) -> dict[str, Any]:
    """One verdict for a group of queries (one symbol/timeframe/length/method/horizon)."""
    match_nets: list[float] = []
    diffs: list[float] = []
    control_nets: list[float] = []
    for record in records:
        if record.realized is None:
            continue
        own = trade_net(direction_from_moves(record.match_moves), record.realized, cost_points)
        ctrl = trade_net(direction_from_moves(record.control_moves), record.realized, cost_points)
        diffs.append((own or 0.0) - (ctrl or 0.0))
        if own is not None:
            match_nets.append(own)
        if ctrl is not None:
            control_nets.append(ctrl)
    trades = len(match_nets)
    mean_net = sum(match_nets) / trades if trades else None
    control_mean = sum(control_nets) / len(control_nets) if control_nets else None
    edge = sum(diffs) / len(diffs) if diffs else None
    p_value = sign_test_pvalue(diffs)
    alpha = ALPHA / max(1, trial_count)
    found = (
        trades >= MIN_TRADES
        and mean_net is not None
        and mean_net > 0
        and edge is not None
        and edge > 0
        and p_value is not None
        and p_value <= alpha
    )
    return {
        "queries": len(diffs),
        "trades": trades,
        "win_rate": (sum(value > 0 for value in match_nets) / trades) if trades else None,
        "mean_net_points": mean_net,
        "control_trades": len(control_nets),
        "control_mean_net_points": control_mean,
        "edge_points": edge,
        "sign_test_p": p_value,
        "bonferroni_alpha": alpha,
        "trial_count": trial_count,
        "verdict": "FOUND" if found else "NOT_FOUND",
    }


def evaluate_cluster(
    member_moves: Sequence[float | None],
    control_moves: Sequence[float | None],
    oos_realized: Sequence[float | None],
    *,
    cost_points: float,
    trial_count: int,
) -> dict[str, Any]:
    """Cluster level: direction from in-sample members, applied to each OOS query."""
    records = [QueryRecord(tuple(member_moves), tuple(control_moves), r) for r in oos_realized]
    return evaluate_group(records, cost_points=cost_points, trial_count=trial_count)


def timestamp_index(candles: Sequence[Any]) -> Mapping[Any, int]:
    """Map candle ``ts_utc`` to its list index, built once per group."""
    return {candle.ts_utc: position for position, candle in enumerate(candles)}
