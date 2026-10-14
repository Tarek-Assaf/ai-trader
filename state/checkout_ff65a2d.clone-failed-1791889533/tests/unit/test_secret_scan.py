"""Tests for the credential scan's detection and safe output behavior."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCANNER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "secret_scan.py"
SPEC = importlib.util.spec_from_file_location("secret_scan", SCANNER_PATH)
assert SPEC is not None and SPEC.loader is not None
SCANNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCANNER)


def test_secret_scan_detects_and_redacts_fake_key(tmp_path: Path, capsys) -> None:
    """A planted fake key is caught without echoing its value."""
    fake = "sk-" + "proj-" + "A" * 32
    source = tmp_path / "sample.py"
    source.write_text(f'api_key = "{fake}"\n', encoding="utf-8")

    findings = SCANNER.scan([source], tmp_path)

    assert [(line, label) for _, line, label in findings] == [
        (1, "OpenAI-style key"),
        (1, "credential assignment"),
    ]
    assert fake not in capsys.readouterr().out


def test_secret_scan_ignores_secret_name_template(tmp_path: Path) -> None:
    """Environment variable names and blank assignments are safe to commit."""
    example = tmp_path / ".env.example"
    example.write_text("OPENAI_API_KEY=\n", encoding="utf-8")

    assert SCANNER.scan([example], tmp_path) == []
