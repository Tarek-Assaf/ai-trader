"""Static transitive import graph excludes execution capabilities from LLM code."""

import ast
import importlib.util
from pathlib import Path


def test_llm_packages_cannot_reach_execution_modules_transitively():
    root = Path(__file__).resolve().parents[2] / "src"
    modules = {}
    for path in (root / "trader").rglob("*.py"):
        parts = path.relative_to(root).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        modules[name] = path
    graph = {}
    for name, path in modules.items():
        imports = set()
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if node.level:
                    target = importlib.util.resolve_name("." * node.level + target, package)
                imports.add(target)
                imports.update(f"{target}.{alias.name}" for alias in node.names)
        graph[name] = imports
    forbidden = tuple(
        f"trader.execution.{part}"
        for part in ("control", "manager", "journal", "leader", "reconciliation", "brokers")
    )
    roots = (
        "trader.agents",
        "trader.lab",
        "trader.research",
        "trader.llm",
        "trader.chat",
        "trader.analysis",
    )
    for start in modules:
        if not start.startswith(roots):
            continue
        visited = set()
        pending = [(start, [start])]
        while pending:
            current, route = pending.pop()
            assert not any(current == p or current.startswith(p + ".") for p in forbidden), route
            if current in visited:
                continue
            visited.add(current)
            pending.extend((target, route + [target]) for target in graph.get(current, ()))


def test_execution_modules_cannot_reach_llm_packages_transitively():
    root = Path(__file__).resolve().parents[2] / "src"
    modules = {}
    for path in (root / "trader").rglob("*.py"):
        parts = path.relative_to(root).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        modules[name] = path
    graph = {}
    for name, path in modules.items():
        imports = set()
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if node.level:
                    target = importlib.util.resolve_name("." * node.level + target, package)
                imports.add(target)
                imports.update(f"{target}.{alias.name}" for alias in node.names)
        graph[name] = imports
    roots = (
        "trader.execution.control",
        "trader.execution.manager",
        "trader.api.control",
        "trader.control",
    )
    forbidden = ("trader.agents", "trader.llm", "trader.research", "trader.chat", "trader.lab")
    for start in roots:
        visited = set()
        pending = [(start, [start])]
        while pending:
            current, route = pending.pop()
            assert not any(current == p or current.startswith(p + ".") for p in forbidden), route
            if current in visited:
                continue
            visited.add(current)
            pending.extend((target, route + [target]) for target in graph.get(current, ()))
