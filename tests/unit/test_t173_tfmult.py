from __future__ import annotations

from datetime import UTC, datetime

from scripts.lab_t173_tfmult import bucket_start


def test_epoch_bucket_uses_tfmult_minutes() -> None:
    stamp = datetime(2026, 1, 1, 0, 17, tzinfo=UTC)
    assert bucket_start(stamp, "epoch", 5) == datetime(2026, 1, 1, 0, 15, tzinfo=UTC)


def test_ny_midnight_bucket_uses_tfmult_minutes() -> None:
    stamp = datetime(2026, 1, 2, 5, 17, tzinfo=UTC)
    assert bucket_start(stamp, "ny_midnight", 5) == datetime(2026, 1, 2, 5, 15, tzinfo=UTC)


def test_tfmult_rejects_non_positive_values() -> None:
    try:
        bucket_start(datetime(2026, 1, 1, tzinfo=UTC), "epoch", 0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("expected a positive tfmult")
