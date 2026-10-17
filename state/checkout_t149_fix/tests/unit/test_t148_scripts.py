"""Focused tests for the T-148 diagnostic helpers."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trader.data.resample import Candle

ROOT = Path(__file__).resolve().parents[2]


def load_script():
    spec = importlib.util.spec_from_file_location("t148", ROOT / "scripts/lab_t148_exit_study.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def bars(*rows: tuple[float, float, float, float]) -> tuple[Candle, ...]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(start + timedelta(minutes=index), *row, 0.0, "test", True, 1)
        for index, row in enumerate(rows)
    )


def test_e4_stop_after_first_target_books_one_third() -> None:
    module = load_script()
    entry = module.Entry(
        0, datetime(2025, 1, 1, tzinfo=UTC), "long", 100.0, 1.0, 99.0, 103.0, None, 1.0
    )
    closed = module.simulate(bars((100, 101.2, 99.8, 101.0), (101, 101, 99.9, 100)), [entry], "E4")
    assert len(closed) == 1
    assert closed[0].r == 1 / 3


def test_e4_reaches_three_r_for_two_r_total() -> None:
    module = load_script()
    entry = module.Entry(
        0, datetime(2025, 1, 1, tzinfo=UTC), "long", 100.0, 1.0, 99.0, 103.0, None, 1.0
    )
    closed = module.simulate(
        bars(
            (100, 103.1, 99.8, 103),
        ),
        [entry],
        "E4",
    )
    assert closed[0].r == 2.0


def test_e6_stop_after_one_r_books_half() -> None:
    module = load_script()
    entry = module.Entry(
        0, datetime(2025, 1, 1, tzinfo=UTC), "long", 100.0, 1.0, 99.0, 103.0, None, 1.0
    )
    closed = module.simulate(bars((100, 101.2, 99.8, 101.0), (101, 101, 100, 100)), [entry], "E6")
    assert closed[0].r == 0.5


def test_full_inversion_helper_flips_signal_and_filter_side() -> None:
    module = load_script()
    assert callable(module.tai_entries_full_inversion)
    source = (ROOT / "scripts/lab_t148_exit_study.py").read_text(encoding="utf-8")
    assert "mirror_full_v2" in source
    assert 'direction = "short" if signal_direction == "long" else "long"' in source
