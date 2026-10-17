"""Instrument-specific idea families: NAS100 and gold get their own seeds."""

import random
from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.backtest import CostModel, run_backtest
from trader.lab.seeds import instrument_seed_specs


def test_each_instrument_gets_its_own_unique_family_on_every_timeframe() -> None:
    for timeframe in (1, 3, 5, 15, 60):
        nas = instrument_seed_specs("NSXUSD", timeframe)
        gold = instrument_seed_specs("XAUUSD", timeframe)
        assert len(nas) == 16 and len(gold) == 20
        assert instrument_seed_specs("EURUSD", timeframe) == ()
        for seeds in (nas, gold):
            names = [seed.name for seed, _ in seeds]
            assert len(names) == len(set(names))
            for _, spec in seeds:
                exits = [rule for rule in spec.exit_rules if rule.kind in {"stop", "target"}]
                assert exits and all(rule.unit == "atr" for rule in exits)
                assert any(rule.kind == "time_stop" for rule in spec.exit_rules)
        assert all(seed.name.startswith("nas_") for seed, _ in nas)
        assert all(seed.name.startswith("gold_") for seed, _ in gold)


def test_gold_ideas_use_the_asian_and_london_sessions() -> None:
    sessions = {
        seed.source: spec.session_filters for seed, spec in instrument_seed_specs("XAUUSD", 15)
    }
    assert sessions["gold_asia_fade"] == ("asia",)
    assert sessions["gold_london_break"] == ("london",)
    assert sessions["gold_us_momentum"] == ("new_york",)


def test_instrument_seeds_run_and_trade_rarely() -> None:
    rng = random.Random(33)
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
    zero = CostModel(
        spread_points={"XAUUSD": 0.0, "NSXUSD": 0.0}, slippage_points={"XAUUSD": 0.0, "NSXUSD": 0.0}
    )
    total = 0
    for instrument in ("XAUUSD", "NSXUSD"):
        for timeframe in (15, 60):
            for _, spec in instrument_seed_specs(instrument, timeframe):
                result = run_backtest(spec, minutes, cost_model=zero)
                total += len(result.trades)
                assert len(result.trades) / 30 < 12.0
    assert total > 0
