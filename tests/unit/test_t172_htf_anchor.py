from __future__ import annotations

from datetime import UTC, datetime

from scripts.lab_t172_htf_anchor import _bucket_start


def test_utc_midnight_anchor_restarts_daily() -> None:
    stamp = datetime(2026, 1, 2, 1, 2, tzinfo=UTC)
    assert _bucket_start(stamp, "utc_midnight") == datetime(2026, 1, 2, 0, 54, tzinfo=UTC)


def test_ny_midnight_anchor_uses_new_york_calendar() -> None:
    stamp = datetime(2026, 1, 2, 5, 2, tzinfo=UTC)
    assert _bucket_start(stamp, "ny_midnight") == datetime(2026, 1, 2, 4, 54, tzinfo=UTC)


def test_cme_session_anchor_has_partial_last_bucket() -> None:
    stamp = datetime(2026, 1, 2, 21, 57, tzinfo=UTC)  # 16:57 New York
    assert _bucket_start(stamp, "cme_session") == datetime(2026, 1, 2, 21, 30, tzinfo=UTC)
