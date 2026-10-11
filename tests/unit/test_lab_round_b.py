"""Round B: time-of-day indicator, time-window seeds, and the payoff-aware diagnostic test."""

import random
from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.backtest import BacktestTrade, CostModel, run_backtest
from trader.lab.engine import _indicator_series
from trader.lab.metrics import _sign_test_p_value
from trader.lab.seeds import round_b_seed_specs
from trader.lab.shadow_stats import mean_profit_p_values


def _candle(stamp: datetime, price: float, symbol: str = "XAUUSD") -> Candle:
    return Candle(
        ts_utc=stamp,
        open=price,
        high=price + 0.2,
        low=price - 0.2,
        close=price,
        volume=1.0,
        series_key=(symbol, "histdata"),
        n_minutes=5,
    )


def _trade(day: int, pnl: float) -> BacktestTrade:
    stamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day)
    return BacktestTrade(
        instrument="NSXUSD",
        direction="long",
        entry_time_utc=stamp,
        exit_time_utc=stamp + timedelta(hours=1),
        entry_price=100.0,
        exit_price=100.0 + pnl,
        size=1.0,
        gross_pnl=pnl,
        costs=0.0,
        net_pnl=pnl,
        exit_reason="target",
    )


def test_utc_minute_is_the_minute_of_the_utc_day() -> None:
    start = datetime(2026, 3, 2, 13, 30, tzinfo=UTC)
    bars = [_candle(start + timedelta(minutes=5 * i), 100.0) for i in range(3)]
    values = _indicator_series("utc_minute", None, bars)
    assert values == (810.0, 815.0, 820.0)


def test_round_b_seeds_are_per_instrument_unique_and_valid() -> None:
    for timeframe in (1, 3, 5, 15, 60):
        gold = round_b_seed_specs("XAUUSD", timeframe)
        nas = round_b_seed_specs("NSXUSD", timeframe)
        assert len(gold) == 8 and len(nas) == 8
        assert round_b_seed_specs("EURUSD", timeframe) == ()
        for seeds in (gold, nas):
            names = [seed.name for seed, _ in seeds]
            assert len(names) == len(set(names))
            for _, spec in seeds:
                assert any(c.indicator == "utc_minute" for c in spec.entry_conditions)
                exits = [rule for rule in spec.exit_rules if rule.kind in {"stop", "target"}]
                assert exits and all(rule.unit == "atr" for rule in exits)
        assert all(seed.name.startswith("gold_data_break") for seed, _ in gold)
        assert all(seed.name.startswith("nas_preopen_break") for seed, _ in nas)


def test_round_b_seeds_only_trade_inside_their_window() -> None:
    rng = random.Random(5)
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    price = 1000.0
    minutes: list[Candle] = []
    for index in range(60 * 24 * 30):
        opening = price
        closing = opening + 0.01 + rng.gauss(0.0, 0.4)
        minutes.append(
            Candle(
                ts_utc=start + timedelta(minutes=index),
                open=opening,
                high=max(opening, closing) + 0.1,
                low=min(opening, closing) - 0.1,
                close=closing,
                volume=1.0,
                series_key=("XAUUSD", "histdata"),
                n_minutes=1,
            )
        )
        price = closing
    zero = CostModel(spread_points={"XAUUSD": 0.0}, slippage_points={"XAUUSD": 0.0})
    total = 0
    for _, spec in round_b_seed_specs("XAUUSD", 15):
        result = run_backtest(spec, minutes, cost_model=zero)
        total += len(result.trades)
        low = min(c.threshold for c in spec.entry_conditions if c.indicator == "utc_minute")
        high = max(c.threshold for c in spec.entry_conditions if c.indicator == "utc_minute")
        for trade in result.trades:
            minute = trade.entry_time_utc.hour * 60 + trade.entry_time_utc.minute
            assert low <= minute <= high + 15  # entry on the bar after the signal bar
    assert total > 0


def test_payoff_aware_test_sees_a_two_to_one_strategy_the_sign_test_calls_luck() -> None:
    # 38 percent winners paying +2 and 62 percent losers paying -1: expectancy +0.14.
    rng = random.Random(1)
    trades = [_trade(day, 2.0 if rng.random() < 0.38 else -1.0) for day in range(1200)]
    shadow = mean_profit_p_values(trades)
    assert _sign_test_p_value(trades) > 0.99  # the gate test: "pure luck"
    assert shadow["diag_mean_pnl_p_trade"] < 0.05
    assert shadow["diag_oos_mean_pnl"] > 0


def test_payoff_aware_test_stays_unconvinced_by_noise_and_small_samples() -> None:
    rng = random.Random(2)
    noise = [_trade(day, rng.choice((-1.0, 1.0))) for day in range(1200)]
    assert mean_profit_p_values(noise)["diag_mean_pnl_p_trade"] > 0.05
    losing = [_trade(day, -0.5) for day in range(200)] + [_trade(day, 0.4) for day in range(200)]
    assert mean_profit_p_values(losing)["diag_mean_pnl_p_trade"] > 0.5
    few = [_trade(day, 5.0) for day in range(10)]
    assert mean_profit_p_values(few)["diag_mean_pnl_p_trade"] == 1.0
    assert mean_profit_p_values(few)["diag_mean_pnl_p_weekly"] == 1.0
