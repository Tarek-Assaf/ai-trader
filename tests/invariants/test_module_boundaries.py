"""Safety boundary checks that remain active as the project grows."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_MODULES = {
    "trader.execution.brokers",
    "trader.integrations.capital_com",
}
LLM_SIDE_DIRS = {"agents", "research", "lab", "chat", "analysis"}


def test_llm_side_modules_do_not_import_broker_implementations() -> None:
    """LLM-facing and research modules cannot import broker implementations."""
    source = ROOT / "src" / "trader"
    for directory in LLM_SIDE_DIRS:
        for path in (source / directory).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported = {node.module or ""}
                else:
                    continue
                assert not any(
                    module == forbidden or module.startswith(forbidden + ".")
                    for module in imported
                    for forbidden in FORBIDDEN_MODULES
                ), f"{path} imports a broker implementation"
