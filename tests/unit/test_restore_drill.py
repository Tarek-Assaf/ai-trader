"""Focused safety and checksum tests for the owner-run restore drill."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "restore_drill"
LOADER = importlib.machinery.SourceFileLoader("restore_drill", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None and SPEC.loader is not None
restore_drill = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = restore_drill
LOADER.exec_module(restore_drill)


def _options(tmp_path: Path, **overrides: object) -> Any:
    values = {
        "scratch_dir": tmp_path / "restore-drill-scratch",
        "scratch_db": "trader_restore_drill",
        "pg_host": "127.0.0.1",
        "pg_port": "5432",
        "pg_user": "trader",
        "admin_db": "postgres",
        "backup_dir": tmp_path / "backups",
        "lake_source": None,
        "lake_manifest": None,
        "dump": None,
        "github_repo": "Tarek-Assaf/ai-trader",
        "github_ref": "backup/state",
        "hf_repo": "Tarek-Assaf/ai-trader-data",
        "skip_remote": True,
        "keep_scratch": True,
    }
    values.update(overrides)
    return restore_drill.DrillOptions(**values)


def test_validate_targets_rejects_live_lake_and_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_DB", "trader")
    with pytest.raises(restore_drill.DrillFailure, match="live data path"):
        restore_drill.validate_targets(
            _options(tmp_path, scratch_dir=Path("/workspace/data/lake/restore-drill"))
        )
    with pytest.raises(restore_drill.DrillFailure, match="live or reserved"):
        restore_drill.validate_targets(_options(tmp_path, scratch_db="trader"))


def test_restore_lake_copies_only_manifest_verified_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = b"verified lake row\n"
    (source / "bars.csv").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    manifest = source / "sha256-manifest.json"
    manifest.write_text(f'{{"files": {{"bars.csv": "{digest}"}}}}', encoding="utf-8")

    count, samples = restore_drill.restore_lake(source, None, tmp_path / "restore-drill-lake")

    assert count == 1
    assert samples == [f"sha256={digest}"]
    assert (tmp_path / "restore-drill-lake" / "bars.csv").read_bytes() == payload


def test_secret_abort_check_does_not_emit_planted_value() -> None:
    # The function asserts the scanner rejects the planted value and only emits
    # its fixed redacted PASS line.
    restore_drill.run_secret_abort_check()
