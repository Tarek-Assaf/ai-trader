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


def test_health_exposes_runtime_unavailable_reason(monkeypatch) -> None:
    """A transient runtime outage has a short, secret-free health reason."""
    monkeypatch.setattr(health, "_probe_database", lambda _url: True)
    monkeypatch.setattr(health, "_probe_redis", lambda _url: True)
    monkeypatch.setenv("ROLE", "worker")
    health.set_runtime_health("runtime_unavailable", "runtime service unavailable: redis")

    payload = health.health_payload()

    assert payload["status"] == "degraded"
    assert payload["runtime_reason"] == "runtime_unavailable"
    assert payload["runtime_detail"] == "runtime service unavailable: redis"
    health.set_runtime_health(None)


def test_health_exposes_live_degraded_runtime(monkeypatch) -> None:
    """A degraded runtime and its last dependency failure make health degraded."""
    monkeypatch.setattr(health, "_probe_database", lambda _url: True)
    monkeypatch.setattr(health, "_probe_redis", lambda _url: True)
    monkeypatch.setenv("ROLE", "worker")
    health.set_runtime_status_provider(
        lambda: {
            "state": "degraded",
            "last_dependency_failure": {
                "stage": "leader.check",
                "error_type": "LeaseLost",
                "message": "service lease expired or ownership changed",
                "at": "2026-10-21T07:00:00+00:00",
            },
        }
    )
    try:
        payload = health.health_payload()
    finally:
        health.set_runtime_status_provider(None)

    assert payload["status"] == "degraded"
    assert payload["runtime"]["state"] == "degraded"
    assert payload["runtime"]["last_dependency_failure"]["stage"] == "leader.check"


def test_health_exposes_tai_demo_outcome_counters(monkeypatch) -> None:
    """Tai outcome telemetry is grouped by epic and contains no price data."""
    monkeypatch.setattr(health, "_probe_database", lambda _url: True)
    monkeypatch.setattr(health, "_probe_redis", lambda _url: True)
    monkeypatch.setenv("ROLE", "worker")
    health.set_tai_demo_health_provider(
        lambda: {
            "NSXUSD": {
                "evaluated": 3,
                "no_signal": 2,
                "market_closed_or_break": 0,
                "stale_or_incomplete": 1,
                "refused": {"spread_too_wide": 1},
                "entry": 0,
                "exit": 0,
                "last_no_signal_gate": "adx",
            }
        }
    )
    try:
        payload = health.health_payload()
    finally:
        health.set_tai_demo_health_provider(None)

    assert payload["tai_demo"]["NSXUSD"]["evaluated"] == 3
    assert payload["tai_demo"]["NSXUSD"]["last_no_signal_gate"] == "adx"
    assert "price" not in str(payload["tai_demo"])


def test_recorder_health_exposes_reconnect_reason_and_market_liveness(tmp_path: Path) -> None:
    """Health exposes the bounded recorder status allowlist without secrets."""
    status_file = tmp_path / "recorder.json"
    status_file.write_text(
        json.dumps(
            {
                "configured": True,
                "status": "reconnecting",
                "ping_reply_ok": False,
                "market_status": "TRADEABLE",
                "market_status_source": "broker",
                "expected_open": True,
                "stale_epics": [],
                "rejected_candles": 2,
                "last_reject_reason": "ask_close_below_bid_close",
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
        "market_status_source": "broker",
        "expected_open": True,
        "stale_epics": [],
        "ticks_received": 0,
        "last_tick_at": None,
        "feed_stalled": False,
        "minutes_since_last_bar": None,
        "rejected_candles": 2,
        "last_reject_reason": "ask_close_below_bid_close",
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
        "market_status_source": "broker",
        "expected_open": False,
        "stale_epics": [],
        "ticks_received": 0,
        "last_tick_at": None,
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


def test_recorder_health_allows_normal_tick_gap_when_market_is_open() -> None:
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    status = {
        "configured": True,
        "status": "healthy",
        "ping_reply_ok": True,
        "market_status": "TRADEABLE",
        "market_status_source": "broker",
        "expected_open": True,
        "stale_epics": [],
        "ticks_received": 1,
        "last_tick_at": (now - timedelta(seconds=45)).isoformat(),
        "last_liveness_at": now.isoformat(),
    }

    assert health._recorder_liveness_ok(status, now=now) is True


def test_recorder_health_rejects_tick_older_than_tick_age_limit() -> None:
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    status = {
        "configured": True,
        "status": "healthy",
        "ping_reply_ok": True,
        "market_status": "TRADEABLE",
        "market_status_source": "broker",
        "expected_open": True,
        "stale_epics": [],
        "ticks_received": 1,
        "last_tick_at": (now - timedelta(seconds=200)).isoformat(),
        "last_liveness_at": now.isoformat(),
    }

    assert health._recorder_liveness_ok(status, now=now) is False


def test_recorder_health_rejects_stale_liveness_with_recent_tick() -> None:
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    status = {
        "configured": True,
        "status": "healthy",
        "ping_reply_ok": True,
        "market_status": "TRADEABLE",
        "market_status_source": "broker",
        "expected_open": True,
        "stale_epics": [],
        "ticks_received": 1,
        "last_tick_at": (now - timedelta(seconds=45)).isoformat(),
        "last_liveness_at": (now - timedelta(seconds=31)).isoformat(),
    }

    assert health._recorder_liveness_ok(status, now=now) is False
