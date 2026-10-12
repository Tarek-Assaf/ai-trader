from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from trader.lab.positive_control import (
    calibrate_drift,
    generate_minutes,
    gross_per_trade,
    minute_timestamps,
    planted_strategy,
)
from trader.lab.spec import ParameterReference


def test_timestamps_skip_weekends_and_align_to_five_minutes() -> None:
    stamps = minute_timestamps(6)
    assert len(stamps) == 6 * 1440
    assert all(stamp.weekday() < 5 for stamp in stamps)
    assert stamps[0].minute == 0 and len(stamps) % 5 == 0


def test_generator_is_deterministic_and_candles_are_valid() -> None:
    first = generate_minutes(7, 3, drift=0.1)
    second = generate_minutes(7, 3, drift=0.1)
    assert first == second
    assert all(c.low <= min(c.open, c.close) and c.high >= max(c.open, c.close) for c in first)


def test_control_strategy_has_a_tunable_parameter_for_the_neighbour_test() -> None:
    spec = planted_strategy()
    parameter = spec.tunable_parameters[0]
    reference = spec.entry_conditions[0].threshold_atr_multiple
    assert isinstance(reference, ParameterReference) and reference.parameter == parameter.name
    assert parameter.maximum <= -1.0 <= parameter.minimum / 1.5 + 1e-9  # neighbours are looser


def test_planted_drift_raises_gross_per_trade_and_null_is_near_zero() -> None:
    null_gross, trades = gross_per_trade(generate_minutes(3, 40, drift=0.0))
    planted_gross, _ = gross_per_trade(generate_minutes(3, 40, drift=0.2))
    assert trades > 50
    assert abs(null_gross) < 2.0
    assert planted_gross > null_gross + 3.0


def test_calibration_bisects_to_target() -> None:
    result = calibrate_drift(6.0, seed=1, days=1, measure=lambda drift: (drift * 30.0, 10))
    assert result["drift_per_minute"] == pytest.approx(0.2, abs=0.01)


def test_script_smoke_with_real_lake_path(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    spec = importlib.util.spec_from_file_location(
        "lab_positive_control", Path(__file__).parents[2] / "scripts" / "lab_positive_control.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["lab_positive_control"] = module
    spec.loader.exec_module(module)
    candles = generate_minutes(5, 130, drift=0.0)
    module.write_lake(candles, tmp_path / "lake")
    entry = module.run_series(
        "smoke",
        tmp_path,
        mode="planted",
        config=Path("config/strategy_lab.yaml"),
        workers=1,
        null_count=5,
    )
    assert entry["attempts"] and entry["attempts"][0]["verdict"] in {"pass", "fail"}
