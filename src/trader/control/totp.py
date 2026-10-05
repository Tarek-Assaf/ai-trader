"""Small RFC 6238 verifier used for owner-only emergency control actions."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
import time


def verify_totp(secret: str, code: str, *, now: float | None = None) -> bool:
    """Verify a six-digit SHA-1 TOTP within the current 30-second window."""
    if not secret or len(code) != 6 or not code.isascii() or not code.isdigit():
        return False
    try:
        key = base64.b32decode(secret.strip().upper() + "=" * (-len(secret.strip()) % 8))
    except (ValueError, binascii.Error):
        return False
    counter = int((time.time() if now is None else now) // 30)
    for step in (counter - 1, counter, counter + 1):
        digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        expected = f"{binary % 1_000_000:06d}"
        if hmac.compare_digest(expected, code):
            return True
    return False
