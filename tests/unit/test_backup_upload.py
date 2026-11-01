"""Offline checks for the incremental and exclusion rules of the backup job."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import types
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


def test_github_header_uses_basic_auth_without_plain_token() -> None:
    token = "github_pat_" + "A" * 40
    header = backup._github_auth_header(token)
    assert header.startswith("Authorization: Basic ")
    assert token not in header


def test_huggingface_fake_client_receives_only_changed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Operation:
        def __init__(self, *, path_in_repo: str, path_or_fileobj: str) -> None:
            self.path_in_repo = path_in_repo
            self.path_or_fileobj = path_or_fileobj

    fake_module = types.ModuleType("huggingface_hub")
    fake_module.CommitOperationAdd = Operation
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)
    source = tmp_path / "changed.parquet"
    source.write_bytes(b"changed")
    manifest = tmp_path / "sha256-manifest.json"
    manifest.write_text('{"files": {"data/changed.parquet": "new"}}\n', encoding="utf-8")

    class FakeApi:
        def __init__(self) -> None:
            self.calls: list[list[Operation]] = []

        def create_commit(self, **kwargs: object) -> None:
            self.calls.append(kwargs["operations"])

    api = FakeApi()
    count = backup.upload_hf_incremental(
        api,
        "Tarek-Assaf/ai-trader-data",
        {"data/changed.parquet": source},
        manifest,
        "hf-secret",
    )

    assert count == 1
    assert [operation.path_in_repo for operation in api.calls[0]] == [
        "data/changed.parquet",
        "sha256-manifest.json",
    ]


def test_planted_secret_aborts_before_either_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = "github_pat_" + "B" * 40
    source = tmp_path / "state.json"
    source.write_text(planted, encoding="utf-8")
    calls: list[str] = []

    github = backup._GitPush(
        repository=tmp_path / "github",
        remote_manifest={},
        changed={"state/state.json": source},
        manifest={},
    )
    huggingface = backup._HfPush(
        api=object(),
        remote_manifest={},
        changed={"data/state.json": source},
        manifest={},
        manifest_path=tmp_path / "sha256-manifest.json",
    )

    def prepare_github(*_args: object, **_kwargs: object) -> backup._GitPush:
        return github

    def prepare_huggingface(*_args: object, **_kwargs: object) -> backup._HfPush:
        return huggingface

    def commit_github(*_args: object, **_kwargs: object) -> int:
        calls.append("github")
        return 1

    def commit_huggingface(*_args: object, **_kwargs: object) -> int:
        calls.append("huggingface")
        return 1

    monkeypatch.setattr(backup, "_prepare_github", prepare_github)
    monkeypatch.setattr(backup, "_prepare_huggingface", prepare_huggingface)
    monkeypatch.setattr(backup, "_commit_github", commit_github)
    monkeypatch.setattr(backup, "_commit_huggingface", commit_huggingface)
    monkeypatch.setenv("GITHUB_BACKUP_TOKEN", "github-secret")
    monkeypatch.setenv("HF_TOKEN", "hf-secret")

    assert backup.main(["--dry-run"]) == 1
    assert calls == []


def test_failure_output_does_not_capture_tokens(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    github_token = "github-secret-value"
    hf_token = "hf-secret-value"
    monkeypatch.setenv("GITHUB_BACKUP_TOKEN", github_token)
    monkeypatch.setenv("HF_TOKEN", hf_token)

    def fail_prepare(*_args: object, **_kwargs: object) -> object:
        raise backup.BackupFailure("backup command failed")

    monkeypatch.setattr(backup, "_prepare_github", fail_prepare)
    assert backup.main(["--dry-run"]) == 1
    output = capsys.readouterr().out
    assert github_token not in output
    assert hf_token not in output


def test_active_files_are_snapshotted_before_hashing(tmp_path: Path) -> None:
    live = tmp_path / "live.parquet"
    live.write_bytes(b"v1")
    old = tmp_path / "old.parquet"
    old.write_bytes(b"old")
    os.utime(old, (1.0, 1.0))
    mapping = {"data/lake/live.parquet": live, "data/lake/old.parquet": old}

    snap = backup._snapshot_active_files(mapping, tmp_path / "snap")  # noqa: SLF001
    live.write_bytes(b"v2-appended")

    assert snap["data/lake/old.parquet"] == old
    assert snap["data/lake/live.parquet"] != live
    assert snap["data/lake/live.parquet"].read_bytes() == b"v1"


def test_extra_account_databases_are_included_in_the_encrypted_upload(
    tmp_path: Path, monkeypatch
) -> None:
    """Account 02's newest dump and its daily copies go up with the main database's."""
    monkeypatch.setattr(
        backup,
        "_run",
        lambda command, env=None: Path(command[command.index("-out") + 1]).write_text("x"),
    )
    root = tmp_path / "backups"
    (root / "accounts" / "02" / "daily").mkdir(parents=True)
    (root / "trader-20260101T000000Z-1.dump").write_text("main")
    for name in ("trader_02-20260101T000000Z-1.dump", "trader_02-20260102T000000Z-1.dump"):
        (root / "accounts" / "02" / name).write_text("account")
    (root / "accounts" / "02" / "daily" / "trader_02-20260102.dump").write_text("account")

    files = backup._encrypted_dumps(root, "key", tmp_path / "out")

    assert sorted(files) == [
        "database/accounts/02/daily/trader_02-20260102.dump.enc",
        "database/accounts/02/trader_02-20260102T000000Z-1.dump.enc",
        "database/trader-20260101T000000Z-1.dump.enc",
    ]
