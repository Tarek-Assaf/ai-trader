#!/usr/bin/env python3
"""Upload generated state and data backups without copying secrets.

The job is intentionally explicit about its roots so a Pod can schedule it at
02:00 UTC and invoke the same command after a strategy stage change. GitHub
receives only generated state on ``backup/state``. Hugging Face receives the
data lake, results, ledger and encrypted database snapshots. Both destinations
use a SHA-256 manifest to avoid re-uploading unchanged files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET_NAMES = ("GITHUB_BACKUP_TOKEN", "GITHUB_TOKEN", "HF_TOKEN", "BACKUP_ENCRYPTION_KEY")
MAX_GITHUB_FILE_BYTES = 50 * 1024 * 1024
EXCLUDED_PARTS = {".git", ".env", "pgdata", "secrets", "__pycache__"}


class BackupFailure(RuntimeError):
    """A fixed, redacted failure suitable for a Pod log."""


@dataclass(frozen=True)
class BackupOptions:
    state_root: Path
    lake_root: Path
    results_root: Path
    ledger_root: Path
    models_root: Path
    backup_root: Path
    github_repo: str
    github_ref: str
    hf_repo: str
    keep_hourly: int
    keep_daily: int
    dry_run: bool


def _env(name: str, *fallbacks: str, default: str = "") -> str:
    for key in (name, *fallbacks):
        value = os.environ.get(key)
        if value:
            return value
    return default


def _run(command: Sequence[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise BackupFailure("required backup command is unavailable") from exc
    if result.returncode != 0:
        raise BackupFailure("backup command failed")
    return result.stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _excluded(relative: Path) -> bool:
    lowered = {part.lower() for part in relative.parts}
    return bool(lowered & EXCLUDED_PARTS) or any(part.lower().endswith((".env", ".secret")) for part in relative.parts)


def collect_files(root: Path, *, max_bytes: int | None = None) -> tuple[Path, ...]:
    """Collect regular files while excluding live DB and secret-shaped paths."""
    if not root.is_dir():
        return ()
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _excluded(relative):
            continue
        if max_bytes is not None and path.stat().st_size > max_bytes:
            continue
        files.append(path)
    return tuple(files)


def build_file_map(root: Path, prefix: str, *, max_bytes: int | None = None) -> dict[str, Path]:
    return {
        f"{prefix.rstrip('/')}/{path.relative_to(root).as_posix()}": path
        for path in collect_files(root, max_bytes=max_bytes)
    }


def load_manifest(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise BackupFailure("backup manifest is unreadable") from exc
    entries = raw.get("files", raw) if isinstance(raw, dict) else {}
    if not isinstance(entries, dict):
        raise BackupFailure("backup manifest has an invalid shape")
    return {
        str(name): str(value).lower()
        for name, value in entries.items()
        if isinstance(name, str) and isinstance(value, str)
    }


def write_manifest(path: Path, files: Mapping[str, Path]) -> dict[str, str]:
    manifest = {name: sha256(source) for name, source in sorted(files.items())}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"files": manifest}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def upload_incremental(
    files: Mapping[str, Path],
    remote_manifest: Mapping[str, str],
    upload: Callable[[str, Path], None],
) -> tuple[dict[str, str], int]:
    """Upload only files whose content hash differs from the remote manifest."""
    manifest: dict[str, str] = {}
    changed = 0
    for name, source in sorted(files.items()):
        digest = sha256(source)
        manifest[name] = digest
        if remote_manifest.get(name) == digest:
            continue
        upload(name, source)
        changed += 1
    return manifest, changed


def _git_env(token: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {token}",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "ai-trader backup",
            "GIT_AUTHOR_EMAIL": "backup@localhost",
            "GIT_COMMITTER_NAME": "ai-trader backup",
            "GIT_COMMITTER_EMAIL": "backup@localhost",
        }
    )
    return env


def _clone(url: str, destination: Path, token: str, branch: str | None = None) -> None:
    args = ["git", "clone", "--depth", "1"]
    if branch:
        args.extend(("--branch", branch))
    args.extend((url, str(destination)))
    try:
        _run(args, env=_git_env(token))
    except BackupFailure:
        if not branch:
            raise
        # A first run has no backup branch. Clone the default branch and create
        # the dedicated state branch without ever pushing to main.
        shutil.rmtree(destination, ignore_errors=True)
        _run(["git", "clone", "--depth", "1", url, str(destination)], env=_git_env(token))
        _run(["git", "checkout", "--quiet", "-b", branch], cwd=destination)


def _push(repository: Path, branch: str, token: str, message: str) -> None:
    _run(["git", "add", "--all"], cwd=repository)
    changed = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=repository, check=False, capture_output=True
    )
    if changed.returncode == 0:
        return
    _run(["git", "commit", "--quiet", "-m", message], cwd=repository, env=_git_env(token))
    _run(["git", "push", "--quiet", "origin", f"HEAD:{branch}"], cwd=repository, env=_git_env(token))


def _secret_scan(root: Path) -> None:
    scanner = REPO_ROOT / "scripts" / "secret_scan.py"
    if not scanner.is_file():
        raise BackupFailure("secret scanner is missing")
    result = subprocess.run(
        [sys.executable, str(scanner), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BackupFailure("secret scan rejected the backup; no upload performed")


def _alert_owner(message: str) -> None:
    token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_IDS")
    if not token or not chat_id:
        return
    body = urllib.parse.urlencode({"chat_id": chat_id.split(",")[0], "text": message}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            return
    except (OSError, ValueError):
        return


def _encrypted_dumps(backup_root: Path, key: str, destination: Path) -> dict[str, Path]:
    dumps = sorted(backup_root.rglob("trader-*.dump")) if backup_root.is_dir() else []
    if not dumps:
        return {}
    if not key:
        raise BackupFailure("database dumps require BACKUP_ENCRYPTION_KEY")
    result: dict[str, Path] = {}
    for source in dumps:
        relative = source.relative_to(backup_root)
        encrypted = destination / relative.parent / f"{source.name}.enc"
        encrypted.parent.mkdir(parents=True, exist_ok=True)
        if not encrypted.is_file() or encrypted.stat().st_mtime < source.stat().st_mtime:
            _run(
                [
                    "openssl",
                    "enc",
                    "-aes-256-cbc",
                    "-pbkdf2",
                    "-in",
                    str(source),
                    "-out",
                    str(encrypted),
                    "-pass",
                    "env:BACKUP_ENCRYPTION_KEY",
                ],
                env={**os.environ, "BACKUP_ENCRYPTION_KEY": key},
            )
        result[f"database/{relative.as_posix()}.enc"] = encrypted
    return result


def github_state_files(options: BackupOptions) -> dict[str, Path]:
    return build_file_map(options.state_root, "state", max_bytes=MAX_GITHUB_FILE_BYTES)


def huggingface_files(options: BackupOptions, encrypted_dir: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    files.update(build_file_map(options.lake_root, "data/lake"))
    files.update(build_file_map(options.results_root, "backtest_results"))
    files.update(build_file_map(options.ledger_root, "experiment_ledger"))
    files.update(build_file_map(options.models_root, "models"))
    files.update(
        _encrypted_dumps(
            options.backup_root,
            _env("BACKUP_ENCRYPTION_KEY"),
            options.backup_root / ".encrypted",
        )
    )
    return files


def _copy_changed(files: Mapping[str, Path], remote: Path, manifest: Mapping[str, str]) -> tuple[dict[str, Path], int]:
    changed: dict[str, Path] = {}
    count = 0
    for name, source in files.items():
        destination = remote / name
        if manifest.get(name) == sha256(source):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        changed[name] = destination
        count += 1
    return changed, count


def push_github_state(options: BackupOptions, files: Mapping[str, Path], token: str, temporary: Path) -> int:
    repository = temporary / "github"
    _clone(f"https://github.com/{options.github_repo}.git", repository, token, options.github_ref)
    remote_manifest = load_manifest(repository / "manifest.json")
    _copy_changed(files, repository, remote_manifest)
    write_manifest(repository / "manifest.json", files)
    _secret_scan(repository)
    if not options.dry_run:
        _push(repository, options.github_ref, token, "backup: update generated state")
    return len(files)


def push_huggingface(options: BackupOptions, files: Mapping[str, Path], token: str, temporary: Path) -> int:
    repository = temporary / "huggingface"
    _clone(f"https://huggingface.co/datasets/{options.hf_repo}", repository, token)
    remote_manifest = load_manifest(repository / "sha256-manifest.json")
    _copy_changed(files, repository, remote_manifest)
    write_manifest(repository / "sha256-manifest.json", files)
    _secret_scan(repository)
    if not options.dry_run:
        _push(repository, "main", token, "backup: update data artifacts")
    return len(files)


def parse_args(argv: Sequence[str] | None = None) -> BackupOptions:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=Path("/workspace/state"))
    parser.add_argument("--lake-root", type=Path, default=Path("/workspace/data/lake"))
    parser.add_argument("--results-root", type=Path, default=Path("/workspace/state/backtests"))
    parser.add_argument("--ledger-root", type=Path, default=Path("/workspace/state/ledger"))
    parser.add_argument("--models-root", type=Path, default=Path("/workspace/models"))
    parser.add_argument("--backup-root", type=Path, default=Path("/workspace/backups"))
    parser.add_argument("--github-repo", default=_env("BACKUP_GITHUB_REPO", default="Tarek-Assaf/ai-trader"))
    parser.add_argument("--github-ref", default="backup/state")
    parser.add_argument("--hf-repo", default=_env("BACKUP_HF_REPO", default="Tarek-Assaf/ai-trader-data"))
    parser.add_argument("--keep-hourly", type=int, default=24)
    parser.add_argument("--keep-daily", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return BackupOptions(
        state_root=args.state_root,
        lake_root=args.lake_root,
        results_root=args.results_root,
        ledger_root=args.ledger_root,
        models_root=args.models_root,
        backup_root=args.backup_root,
        github_repo=args.github_repo,
        github_ref=args.github_ref,
        hf_repo=args.hf_repo,
        keep_hourly=max(1, args.keep_hourly),
        keep_daily=max(1, args.keep_daily),
        dry_run=args.dry_run,
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        options = parse_args(argv)
        github_token = _env("GITHUB_BACKUP_TOKEN", "GITHUB_TOKEN")
        hf_token = _env("HF_TOKEN")
        if not github_token or not hf_token:
            raise BackupFailure("backup upload requires configured destination tokens")
        with tempfile.TemporaryDirectory(prefix="backup-upload-") as directory:
            temporary = Path(directory)
            github_files = github_state_files(options)
            hf_files = huggingface_files(options, temporary / "encrypted")
            staging = temporary / "staging"
            for name, source in {**github_files, **hf_files}.items():
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            _secret_scan(staging)
            github_count = push_github_state(options, github_files, github_token, temporary)
            hf_count = push_huggingface(options, hf_files, hf_token, temporary)
        print(f"PASS backup upload: github_files={github_count} hf_files={hf_count} changed files are hash-filtered")
        return 0
    except BackupFailure as exc:
        if "secret scan" in str(exc):
            _alert_owner("Backup upload aborted: secret scan rejected the payload (value redacted).")
        print(f"FAIL backup upload: {exc}")
        return 1
    except (OSError, ValueError):
        print("FAIL backup upload: invalid or unavailable backup input")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
