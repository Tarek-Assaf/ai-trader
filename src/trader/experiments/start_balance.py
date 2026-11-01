"""The account's starting balance, recorded once from the broker instead of typed by the owner.

Lot size is ``base lot x balance / start balance``.  Until now the owner typed the start balance
into ``TAI_DEMO_START_BALANCE``; a stale or wrong value silently changed every lot.  This store
keeps the balance the account had the first time the bot sized a trade, in the account's own
workspace, so it survives restarts and container re-creates and never needs editing.

A file that exists but cannot be read is an error, not "missing": re-recording would quietly change
every later lot size.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


class StartBalanceUnreadable(RuntimeError):
    """The saved start balance exists but is not a usable amount."""


class StartBalanceStore:
    """One saved amount per account workspace; written once, read forever."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> Decimal | None:
        """The saved start balance, or None when nothing has been saved yet."""
        with self._lock:
            return self._read()

    def record_if_missing(self, balance: Decimal) -> tuple[Decimal, bool]:
        """Save ``balance`` if nothing is saved; return (value in force, whether it is new)."""
        if not isinstance(balance, Decimal) or not balance.is_finite() or balance <= 0:
            raise StartBalanceUnreadable("the account balance to record must be above zero")
        with self._lock:
            existing = self._read()
            if existing is not None:
                return existing, False
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "start_balance": str(balance),
                "recorded_at": datetime.now(UTC).isoformat(),
            }
            handle, temp_name = tempfile.mkstemp(
                dir=self._path.parent, prefix=".start_balance.", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, self._path)
            except BaseException:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                raise
            return balance, True

    def _read(self) -> Decimal | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StartBalanceUnreadable(f"{self._path.name} cannot be read") from exc
        try:
            value = Decimal(str(json.loads(raw)["start_balance"]))
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise StartBalanceUnreadable(
                f"{self._path.name} is damaged; delete it to record the balance again"
            ) from exc
        if not value.is_finite() or value <= 0:
            raise StartBalanceUnreadable(
                f"{self._path.name} holds an unusable amount; delete it to record it again"
            )
        return value
