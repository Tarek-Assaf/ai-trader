"""Small deterministic tests for the offline Strategy Lab controls."""

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trader.data.resample import Candle
from trader.lab.backtest import CostModel

SCRIPT = Path(__file__).parents[2] / "scripts" / "lab_controls.py"
SPEC = importlib.util.spec_from_file_location("lab_controls", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
controls = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = controls
SPEC.loader.exec_module(controls)
perfect_foresight_control = controls.perfect_foresight_control
random_entry_controls = controls.random_entry_controls


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
    result = perfect_foresight_control(_bars(), instrument="NSXUSD", cost_model=CostModel())
    assert result.trades >= 20
    assert result.gross_per_trade > 0.2
    assert result.win_rate > 0.9
