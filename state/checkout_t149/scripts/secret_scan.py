"""Detect common real-looking credential patterns without displaying matched values."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PATTERNS = {
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "Anthropic-style key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?"
        r"(?!your_|change_me|replace_me|example|dummy|test_)[A-Za-z0-9_./+=-]{24,}"
    ),
}
IGNORED_PARTS = {
    ".git",
    ".venv",
    ".tools",
    ".uv-cache",
    ".uv-tmp",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}


def tracked_paths(root: Path) -> list[Path]:
    """Return tracked Git files plus untracked non-ignored files for local use."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    except (OSError, subprocess.CalledProcessError):
        return [path for path in root.rglob("*") if path.is_file()]


def scan(paths: Sequence[Path], root: Path) -> list[tuple[Path, int, str]]:
    """Return finding locations and pattern labels, never matched credential text."""
    findings: list[tuple[Path, int, str]] = []
    for path in paths:
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts) or path.name == ".env.example":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((relative, line_number, label))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("paths", nargs="*", type=Path, help="files or directories to scan")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    selected: list[Path] = []
    for item in args.paths:
        candidate = (root / item).resolve() if not item.is_absolute() else item.resolve()
        selected.extend(candidate.rglob("*") if candidate.is_dir() else [candidate])
    findings = scan(selected or tracked_paths(root), root)
    if findings:
        for path, line, label in findings:
            print(f"{path}:{line}: possible {label} (value redacted)")
        print(f"Secret scan failed: {len(findings)} possible credential(s).")
        return 1
    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
