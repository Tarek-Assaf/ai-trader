"""Regime-filtered trend and ranging reversion seeds are valid, unique and ATR-scaled."""

import random
from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.backtest import CostModel, run_backtest
from trader.lab.seeds import swing_seed_specs


def _bars(count: int = 4000) -> list[Candle]:
    rng = random.Random(5)
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    price = 1000.0
    rows: list[Candle] = []
    for index in range(count):
        opening = price
        closing = opening + rng.gauss(0.0, 1.0)
        rows.append(
            Candle(
                ts_utc=start + timedelta(minutes=5 * index),
                open=opening,
                high=max(opening, closing) + 0.4,
                low=min(opening, closing) - 0.4,
                close=closing,
                volume=1.0,
                series_key=("NSXUSD", "histdata"),
                n_minutes=5,
            )
        )
        price = closing
    return rows


def test_swing_seeds_are_unique_valid_atr_scaled_and_regime_filtered() -> None:
    seeds = swing_seed_specs("NSXUSD", 5)
    names = [seed.name for seed, _ in seeds]
    assert len(names) == len(set(names)) == 24
    for _, spec in seeds:
        assert spec.regime_filters
        exits = [rule for rule in spec.exit_rules if rule.kind in {"stop", "target"}]
        assert exits and all(rule.unit == "atr" for rule in exits)
        assert any(rule.kind == "time_stop" for rule in spec.exit_rules)


def test_swing_seeds_run_and_trade_rarely() -> None:
    bars = _bars()
    zero = CostModel(spread_points={"NSXUSD": 0.0}, slippage_points={"NSXUSD": 0.0})
    days = len(bars) / 288
    for _, spec in swing_seed_specs("NSXUSD", 5):
        result = run_backtest(spec, bars, cost_model=zero)
        assert len(result.trades) / days < 8.0


def test_slow_and_swing_seeds_keep_wall_clock_length_across_timeframes() -> None:
    from trader.lab.seeds import slow_seed_specs

    def channel_of(spec: object) -> int:
        condition = spec.entry_conditions[0]  # type: ignore[attr-defined]
        return int(condition.threshold_period)

    five = {s.name: channel_of(sp) for s, sp in slow_seed_specs("NSXUSD", 5)}
    sixty = {s.name: channel_of(sp) for s, sp in slow_seed_specs("NSXUSD", 60)}
    one = {s.name: channel_of(sp) for s, sp in slow_seed_specs("NSXUSD", 1)}
    assert five["slow_trend_long_288_2_4_all"] == 288
    assert sixty["slow_trend_long_288_2_4_all"] == 24
    assert one["slow_trend_long_288_2_4_all"] == 1440
    for timeframe in (15, 60):
        assert len(slow_seed_specs("XAUUSD", timeframe)) == 24
        assert len(swing_seed_specs("XAUUSD", timeframe)) == 24


def test_slow_seeds_backtest_on_15m_and_60m_bars() -> None:
    from trader.lab.seeds import slow_seed_specs

    rng = random.Random(9)
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    price = 1000.0
    minutes: list[Candle] = []
    for index in range(60 * 24 * 40):
        opening = price
        closing = opening + 0.01 + rng.gauss(0.0, 0.3)
        minutes.append(
            Candle(
                ts_utc=start + timedelta(minutes=index),
                open=opening,
                high=max(opening, closing) + 0.1,
                low=min(opening, closing) - 0.1,
                close=closing,
                volume=1.0,
                series_key=("NSXUSD", "histdata"),
                n_minutes=1,
            )
        )
        price = closing
    zero = CostModel(spread_points={"NSXUSD": 0.0}, slippage_points={"NSXUSD": 0.0})
    for timeframe in (15, 60):
        _, spec = slow_seed_specs("NSXUSD", timeframe)[0]
        result = run_backtest(spec, minutes, cost_model=zero)
        assert result.starting_equity > 0
