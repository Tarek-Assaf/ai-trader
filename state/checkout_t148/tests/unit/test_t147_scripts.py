"""Pure helper coverage for the T-147 diagnostic script."""

from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trader.data.resample import Candle

ROOT = Path(__file__).resolve().parents[2]


def load_function(name: str):
    tree = ast.parse((ROOT / "scripts/lab_tai_smart_filter.py").read_text(encoding="utf-8"))
    node = next(
        item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name
    )
    namespace: dict[str, object] = {
        "Candle": Candle,
        "math": __import__("math"),
        "timedelta": timedelta,
        "replace": replace,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), name, "exec"), namespace)
    return namespace[name]


def test_heikin_ashi_uses_only_current_and_previous_closed_bars() -> None:
    heikin_ashi = load_function("heikin_ashi")
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = (
        Candle(start, 10, 12, 9, 11),
        Candle(start + timedelta(minutes=1), 11, 13, 10, 12),
    )
    result = heikin_ashi(bars)
    assert result[0].close == 10.5
    assert result[0].open == 10.5
    assert result[1].open == (result[0].open + result[0].close) / 2
    assert result[1].close == 11.5


def test_variant_set_is_bounded_and_reproducible() -> None:
    source = (ROOT / "scripts/lab_tai_smart_filter.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [
        item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "TaiParams"
    ]
    namespace: dict[str, object] = {"replace": replace, "dataclass": dataclass}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "params", "exec"), namespace)
    TaiParams = namespace["TaiParams"]
    function_node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "variant_params"
    )
    exec(
        compile(ast.Module(body=[function_node], type_ignores=[]), "variant_params", "exec"),
        namespace,
    )
    variant_params = namespace["variant_params"]
    variants = variant_params(TaiParams())
    assert len(variants) == 15
    assert variants[0] == TaiParams()
    assert len({tuple(getattr(item, field) for field in item.__slots__) for item in variants}) == 15


def test_scale_out_accounting_matches_registered_r_values() -> None:
    real_r = load_function("_real_r")
    dashboard_r = load_function("_dashboard_r")
    assert real_r(0, True) == -1.0
    assert real_r(1, True) == 1 / 3
    assert real_r(2, True) == 1.0
    assert real_r(3, False) == 2.0
    assert dashboard_r(1, False) == 1.0
    assert dashboard_r(3, False) == 3.0


def test_dashboard_books_highest_target_when_stop_follows_tp1() -> None:
    real_r = load_function("_real_r")
    dashboard_r = load_function("_dashboard_r")
    assert real_r(1, True) == 1 / 3
    assert dashboard_r(1, True) == 1.0
    assert dashboard_r(0, True) == -1.0
