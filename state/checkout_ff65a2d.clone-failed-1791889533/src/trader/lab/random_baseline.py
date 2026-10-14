"""Diagnostic: does a strategy beat random entries that use the same exits?

Read-only and not a gate input.  A long-only idea on an index that rose for 16
years can show a profit factor above 1 simply because any long entry with a
2:1 ATR exit rode the drift.  This module replays the SAME strategy exits with
entries placed at random bars (matched to the strategy's own New York hour of
day and to its number of signals) many times, then reports where the real
strategy's net profit sits inside that random distribution.  A percentile near
50 means the entry rule adds nothing beyond the exits and the market drift.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Iterable, Sequence
from statistics import fmean, pstdev
from zoneinfo import ZoneInfo

from trader.data.resample import Candle, resample_ohlc
from trader.lab.backtest import CostModel, run_backtest
from trader.lab.engine import EntrySignal, evaluate_entries
from trader.lab.spec import StrategySpec

_NEW_YORK = ZoneInfo("America/New_York")


def _profit_factor(pnls: Sequence[float]) -> float | None:
    gains = sum(value for value in pnls if value > 0)
    losses = -sum(value for value in pnls if value < 0)
    if losses <= 0:
        return None
    return gains / losses


def random_entry_baseline(
    spec: StrategySpec,
    candles: Iterable[Candle],
    *,
    cost_model: CostModel | None = None,
    trials: int = 100,
    seed: int = 7,
) -> dict[str, object]:
    """Compare the strategy's net profit with ``trials`` random-entry replays."""
    if trials < 10:
        raise ValueError("trials must be at least 10")
    source = list(candles)
    bars = [bar for bar in resample_ohlc(source, spec.timeframe_minutes) if bar.complete]
    real_signals = evaluate_entries(spec, bars)
    real = run_backtest(spec, bars, cost_model=cost_model, entry_signals=real_signals)
    if not real_signals or not real.trades:
        return {"signals": len(real_signals), "note": "strategy produced no trades"}
    by_hour: dict[int, list[int]] = defaultdict(list)
    for index, bar in enumerate(bars):
        by_hour[bar.ts_utc.astimezone(_NEW_YORK).hour].append(index)
    stamp_hours = [signal.timestamp_utc.astimezone(_NEW_YORK).hour for signal in real_signals]
    rng = random.Random(seed)
    random_nets: list[float] = []
    random_pfs: list[float] = []
    random_trades: list[int] = []
    for _ in range(trials):
        picks: dict[int, EntrySignal] = {}
        for hour in stamp_hours:
            pool = by_hour.get(hour)
            if not pool:
                continue
            index = rng.choice(pool)
            bar = bars[index]
            picks[index] = EntrySignal(
                instrument=spec.instrument,
                timeframe_minutes=spec.timeframe_minutes,
                timestamp_utc=bar.ts_utc,
                close=bar.close,
            )
        signals = tuple(picks[index] for index in sorted(picks))
        result = run_backtest(spec, bars, cost_model=cost_model, entry_signals=signals)
        random_nets.append(float(result.net_pnl))
        random_trades.append(len(result.trades))
        factor = _profit_factor([float(trade.net_pnl) for trade in result.trades])
        if factor is not None:
            random_pfs.append(factor)
    net = float(real.net_pnl)
    mean, spread = fmean(random_nets), pstdev(random_nets)
    below = sum(1 for value in random_nets if value <= net)
    real_pf = _profit_factor([float(trade.net_pnl) for trade in real.trades])
    return {
        "signals": len(real_signals),
        "trades": len(real.trades),
        "trials": trials,
        "strategy_net": round(net, 2),
        "strategy_profit_factor": None if real_pf is None else round(real_pf, 3),
        "random_net_mean": round(mean, 2),
        "random_net_sd": round(spread, 2),
        "random_net_p95": round(sorted(random_nets)[int(0.95 * (trials - 1))], 2),
        "random_profit_factor_mean": round(fmean(random_pfs), 3) if random_pfs else None,
        "random_trades_mean": round(fmean(random_trades), 1),
        "strategy_percentile": round(below / trials, 3),
        "z_vs_random": round((net - mean) / spread, 2) if spread > 0 else None,
    }


__all__ = ["random_entry_baseline"]
