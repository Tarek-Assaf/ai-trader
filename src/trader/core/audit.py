"""Hash-chain helpers for independently checking append-only audit records."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping


def audit_hash(previous_hash: str, payload: str) -> str:
    """Hash the exact UTF-8 payload preceded by its prior chain hash."""
    return hashlib.sha256((previous_hash + payload).encode("utf-8")).hexdigest()


def verify_audit_chain(entries: Iterable[Mapping[str, str]]) -> bool:
    """Verify ordered audit entries against their prior hash and payload."""
    previous_hash = ""
    for entry in entries:
        if entry.get("prev_hash") != previous_hash:
            return False
        expected_hash = audit_hash(previous_hash, entry.get("payload", ""))
        if entry.get("hash") != expected_hash:
            return False
        previous_hash = expected_hash
    return True
