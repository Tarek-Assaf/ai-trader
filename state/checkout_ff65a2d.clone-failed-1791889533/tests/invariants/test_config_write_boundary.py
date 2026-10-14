"""Only SettingsService may write protected configuration files."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTECTED_NAMES = {"risk" + ".yaml", "risk_ceilings" + ".yaml", "strategy_lab" + ".yaml"}
ALLOWED_MODULE = Path("src/trader/control/settings_service.py")


def test_only_settings_service_mentions_protected_write_targets() -> None:
    """Protect risk and strategy gate files from direct agent/tool writes."""
    write_methods = {"write_text", "write_bytes", "unlink", "replace", "rename"}
    for area in ("src", "scripts", "agent_state"):
        for path in (ROOT / area).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            parents: dict[ast.AST, ast.AST] = {
                child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if any(
                    name in node.value.replace("\\", "/").split("/") for name in PROTECTED_NAMES
                ):
                    ancestor = node
                    is_write_target = False
                    while ancestor in parents:
                        ancestor = parents[ancestor]
                        if isinstance(ancestor, ast.Call):
                            function = ancestor.func
                            if (
                                isinstance(function, ast.Attribute)
                                and function.attr in write_methods
                            ):
                                is_write_target = True
                                break
                    if is_write_target:
                        assert path.relative_to(ROOT).as_posix() == ALLOWED_MODULE.as_posix(), (
                            f"{path} writes a protected config path"
                        )
