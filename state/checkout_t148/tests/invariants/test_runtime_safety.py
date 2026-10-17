"""Runtime orchestration must stay behind the existing execution boundary."""

from __future__ import annotations

from pathlib import Path


def test_runtime_does_not_import_or_call_broker_adapters() -> None:
    source = Path("src/trader/runtime/composition.py").read_text(encoding="utf-8")
    assert "execution.brokers" not in source
    assert ".place_order(" not in source
    assert ".close_position(" not in source
