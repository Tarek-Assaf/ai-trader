"""Operational health reports backup freshness and build identity."""

from __future__ import annotations

import json
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
    assert age in (74, 75)
    assert fresh is True


def test_health_is_degraded_when_role_all_backup_is_stale(tmp_path: Path, monkeypatch) -> None:
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


def test_health_is_degraded_when_role_all_has_no_backup(tmp_path: Path, monkeypatch) -> None:
    """ROLE=all reports a missing first backup as degraded."""
    monkeypatch.setattr(health, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(health, "_probe_database", lambda _url: True)
    monkeypatch.setattr(health, "_probe_redis", lambda _url: True)
    monkeypatch.setenv("ROLE", "all")

    payload = health.health_payload()

    assert payload["status"] == "degraded"
    assert payload["last_backup_at"] is None
    assert payload["last_backup_age_seconds"] is None


def test_recorder_health_exposes_reconnect_reason_and_market_liveness(tmp_path: Path) -> None:
    """Health uses ping and market status; a quiet but open price feed is not inspected."""
    status_file = tmp_path / "recorder.json"
    status_file.write_text(
        json.dumps(
            {
                "configured": True,
                "status": "reconnecting",
                "ping_reply_ok": False,
                "market_status": "TRADEABLE",
                "reconnect_count": 3,
                "last_reconnect_reason": "ConnectionClosed",
                "last_liveness_at": "2026-10-03T12:00:00+00:00",
                "secret": "must-not-be-exposed",
            }
        ),
        encoding="utf-8",
    )

    value = health._data_recorder_status(status_file)

    assert value == {
        "configured": True,
        "status": "reconnecting",
        "ping_reply_ok": False,
        "market_status": "TRADEABLE",
        "reconnect_count": 3,
        "last_reconnect_reason": "ConnectionClosed",
        "last_update_at": None,
        "last_closed_bar_at": None,
        "last_liveness_at": "2026-10-03T12:00:00+00:00",
    }


def test_recorder_health_requires_fresh_ping_and_market_status() -> None:
    """CLOSED markets stay healthy when protocol heartbeats continue; stale heartbeats fail."""
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    status = {
        "configured": True,
        "status": "healthy",
        "ping_reply_ok": True,
        "market_status": "CLOSED",
        "last_liveness_at": now.isoformat(),
    }

    assert health._recorder_liveness_ok(status, now=now) is True
    assert (
        health._recorder_liveness_ok(
            {**status, "last_liveness_at": (now - timedelta(seconds=31)).isoformat()}, now=now
        )
        is False
    )
    assert health._recorder_liveness_ok({**status, "ping_reply_ok": False}, now=now) is False
