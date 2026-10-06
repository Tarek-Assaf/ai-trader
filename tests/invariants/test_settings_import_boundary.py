"""LLM-facing packages must not reach the deterministic settings service."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = "trader.control.settings_service"
LLM_AREAS = (
    ROOT / "src/trader/agents",
    ROOT / "src/trader/research",
    ROOT / "src/trader/lab",
    ROOT / "src/trader/analysis",
)


def test_llm_side_packages_cannot_import_settings_service() -> None:
    for area in LLM_AREAS:
        if not area.exists():
            continue
        for path in area.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = (alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    names = (node.module or "",)
                else:
                    continue
                assert FORBIDDEN not in names, f"{path} imports the settings service"
