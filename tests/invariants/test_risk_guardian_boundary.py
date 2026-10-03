from __future__ import annotations

import ast
from pathlib import Path


def test_risk_guardian_has_no_broker_or_llm_dependency() -> None:
    source = Path("src/trader/risk/guardian.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))

    assert not any(
        name.startswith(("trader.integrations", "trader.agents", "trader.llm")) for name in imports
    )


def test_risk_guardian_does_not_place_or_size_orders() -> None:
    source = Path("src/trader/risk/guardian.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert not methods.intersection(
        {"place_order", "modify_order", "close_position", "calculate_size", "size_position"}
    )
