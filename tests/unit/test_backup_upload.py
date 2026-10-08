"""Offline checks for the incremental and exclusion rules of the backup job."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "backup_upload.py"
SPEC = importlib.util.spec_from_file_location("backup_upload", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
backup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup
SPEC.loader.exec_module(backup)


def test_incremental_upload_skips_unchanged_files(tmp_path: Path) -> None:
    unchanged = tmp_path / "unchanged.txt"
    changed = tmp_path / "changed.txt"
    unchanged.write_text("same", encoding="utf-8")
    changed.write_text("new", encoding="utf-8")
    same_hash = hashlib.sha256(b"same").hexdigest()
    uploaded: list[str] = []

    manifest, count = backup.upload_incremental(
        {"data/unchanged.txt": unchanged, "data/changed.txt": changed},
        {"data/unchanged.txt": same_hash},
        lambda name, _: uploaded.append(name),
    )

    assert count == 1
    assert uploaded == ["data/changed.txt"]
    assert manifest["data/unchanged.txt"] == same_hash


def test_collect_files_excludes_env_and_pgdata(tmp_path: Path) -> None:
    (tmp_path / "kept.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=not-for-backup", encoding="utf-8")
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    (pgdata / "base").write_text("database", encoding="utf-8")

    files = backup.collect_files(tmp_path)

    assert [path.name for path in files] == ["kept.json"]


def test_build_file_map_uses_posix_paths(tmp_path: Path) -> None:
    source = tmp_path / "state"
    source.mkdir()
    (source / "report.json").write_text("{}", encoding="utf-8")

    mapped: dict[str, Path] = backup.build_file_map(source, "state")

    assert list(mapped) == ["state/report.json"]


def test_secret_scan_blocks_staged_planted_value(tmp_path: Path) -> None:
    planted = "github_pat_" + "A" * 40
    (tmp_path / "staged.txt").write_text(f"token={planted}\n", encoding="utf-8")

    with pytest.raises(backup.BackupFailure, match="no upload"):
        backup._secret_scan(tmp_path)
