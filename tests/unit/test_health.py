"""Operational health reports backup freshness and build identity."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trader.api import health


def test_backup_freshness_uses_newest_nonempty_dump(tmp_path: Path) -> None:
    """Freshness derives from the newest completed dump's mtime."""
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    older = tmp_path / "trader-old.dump"
    newest = tmp_path / "trader-new.dump"
    older.write_bytes(b"old")
    newest.write_bytes(b"new")
    older_time = (now - timedelta(minutes=10)).timestamp()
    newest_time = (now - timedelta(seconds=75)).timestamp()
    os.utime(older, (older_time, older_time))
    os.utime(newest, (newest_time, newest_time))

    timestamp, age, fresh = health._backup_freshness(tmp_path, now=now)

    assert timestamp == datetime.fromtimestamp(newest_time, UTC).isoformat()
    assert age == 75
    assert fresh is True


def test_health_is_degraded_when_role_all_backup_is_stale(
    tmp_path: Path, monkeypatch
) -> None:
    """ROLE=all requires a backup no older than fifteen minutes."""
    old_backup = tmp_path / "trader-stale.dump"
    old_backup.write_bytes(b"dump")
    stale_time = (datetime.now(UTC) - timedelta(minutes=16)).timestamp()
    os.utime(old_backup, (stale_time, stale_time))
    monkeypatch.setattr(health, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(health, "_probe_database", lambda _url: True)
    monkeypatch.setattr(health, "_probe_redis", lambda _url: True)
    monkeypatch.setenv("ROLE", "all")
    monkeypatch.setenv("GIT_SHA", "test-build-sha")

    payload = health.health_payload()

    assert payload["status"] == "degraded"
    assert payload["version"] == "test-build-sha"
    assert payload["last_backup_age_seconds"] >= 900


def test_health_is_degraded_when_role_all_has_no_backup(
    tmp_path: Path, monkeypatch
) -> None:
    """ROLE=all reports a missing first backup as degraded."""
    monkeypatch.setattr(health, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(health, "_probe_database", lambda _url: True)
    monkeypatch.setattr(health, "_probe_redis", lambda _url: True)
    monkeypatch.setenv("ROLE", "all")

    payload = health.health_payload()

    assert payload["status"] == "degraded"
    assert payload["last_backup_at"] is None
    assert payload["last_backup_age_seconds"] is None
