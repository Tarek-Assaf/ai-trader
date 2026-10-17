"""The package layout remains aligned with ARCHITECTURE.md section 11."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIRS = (
    "core",
    "integrations",
    "data",
    "analysis",
    "research",
    "agents",
    "risk",
    "execution",
    "lab",
    "backtest",
    "chat",
    "control",
    "viz",
    "notify",
    "reports",
    "backup",
    "memory",
    "scaling",
    "workers",
    "api",
    "dashboard",
)


def test_architecture_packages_are_importable() -> None:
    """Each top-level architecture package has an initializer."""
    for name in PACKAGE_DIRS:
        package = ROOT / "src" / "trader" / name
        assert (package / "__init__.py").is_file(), f"Missing importable package: {name}"
