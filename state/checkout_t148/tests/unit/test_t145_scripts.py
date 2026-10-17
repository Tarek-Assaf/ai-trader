"""Import-only unit coverage for the T-145 diagnostic helpers."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_function(relative: str, function_name: str):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == function_name
    )
    namespace: dict[str, object] = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), relative, "exec"), namespace)
    return namespace[function_name]


def test_distinct_top_keeps_distinct_hash_and_metric_pairs() -> None:
    distinct_top = load_function("scripts/lab_by_year.py", "distinct_top")
    rows = [
        {"strategy_hash": "a", "trades": 10, "profit_factor": 1.2},
        {"strategy_hash": "a", "trades": 11, "profit_factor": 1.3},
        {"strategy_hash": "b", "trades": 10, "profit_factor": 1.2},
        {"strategy_hash": "c", "trades": 12, "profit_factor": 1.4},
    ]
    selected = distinct_top(rows)
    assert [row["strategy_hash"] for row in selected] == ["a", "c"]


def test_renderer_metric_keeps_required_fields() -> None:
    renderer = load_script("t145_renderer", "scripts/render_t145_by_year.py")
    stats = {"trades": 4, "net_points": 3.5, "profit_factor": 1.25, "max_drawdown_points": 2.0}
    assert renderer.metric(stats) == stats
    assert renderer.n(3.500) == "3.5"
    assert renderer.n(None) == "n/a"
