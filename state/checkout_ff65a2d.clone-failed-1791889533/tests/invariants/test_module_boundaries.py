"""Safety boundary checks that remain active as the project grows."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from trader.data.recorder import CapitalDataRecorder
from trader.integrations.capital_com.client import CapitalComClient

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_MODULES = {
    "trader.execution.brokers",
    "trader.integrations.capital_com",
    "trader.data.recorder",
}
LLM_SIDE_DIRS = {"agents", "research", "lab", "chat", "analysis"}
FORBIDDEN_BROKER_METHOD = re.compile(r"(order|position|settings)", re.IGNORECASE)


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


def test_capital_market_data_surfaces_have_no_trading_or_settings_methods() -> None:
    """The stream recorder and read-only broker client cannot place trades or edit config."""
    for surface in (CapitalComClient, CapitalDataRecorder):
        public_names = (name for name in dir(surface) if not name.startswith("_"))
        assert not [name for name in public_names if FORBIDDEN_BROKER_METHOD.search(name)]
