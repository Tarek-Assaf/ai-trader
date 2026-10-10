"""Small deterministic tests for the offline Strategy Lab controls."""

from datetime import UTC, datetime, timedelta

from trader.data.resample import Candle
from trader.lab.backtest import CostModel
from scripts.lab_controls import perfect_foresight_control, random_entry_controls


def _bars(count: int = 80) -> list[Candle]:
    start = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    rows: list[Candle] = []
    price = 100.0
    for index in range(count):
        direction = 1.0 if index % 2 == 0 else -1.0
        opening = price
        closing = opening + direction
        rows.append(
            Candle(
                ts_utc=start + timedelta(minutes=5 * index),
                open=opening,
                high=max(opening, closing) + 0.5,
                low=min(opening, closing) - 0.5,
                close=closing,
                volume=1.0,
                series_key=("NSXUSD", "histdata"),
                n_minutes=5,
            )
        )
        price = closing
    return rows


def test_random_entry_controls_are_reproducible_and_near_zero() -> None:
    bars = _bars()
    first = random_entry_controls(
        bars,
        instrument="NSXUSD",
        cost_model=CostModel(),
        seeds=(0, 1, 2, 3, 4),
    )
    second = random_entry_controls(
        bars,
        instrument="NSXUSD",
        cost_model=CostModel(),
        seeds=(0, 1, 2, 3, 4),
    )
    assert first == second
    assert len(first["seeds"]) == 5
    assert all(abs(float(row["gross_per_trade"])) <= 2.0 for row in first["seeds"])


def test_perfect_foresight_control_is_strongly_positive_gross() -> None:
    result = perfect_foresight_control(
        _bars(), instrument="NSXUSD", cost_model=CostModel()
    )
    assert result.trades >= 20
    assert result.gross_per_trade > 0.2
    assert result.win_rate > 0.9
