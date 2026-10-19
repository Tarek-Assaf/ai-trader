from datetime import UTC, datetime

from scripts.lab_t174_renko import RenkoBuilder, bucket_start


def test_renko_continuation_and_two_brick_reversal() -> None:
    builder = RenkoBuilder()
    for price in (100.0, 101.0, 102.0, 103.0):
        builder.add(price, 1.0)
    assert builder.bricks == 3
    assert builder.direction == 1
    assert builder.last_close == 103.0

    builder.add(101.0, 1.0)
    assert builder.bricks == 5
    assert builder.direction == -1
    assert builder.last_close == 101.0


def test_bucket_start_rejects_unknown_anchor() -> None:
    try:
        bucket_start(datetime.now(UTC), "bad")
    except ValueError as exc:
        assert "unknown anchor" in str(exc)
    else:
        raise AssertionError("unknown anchor should fail")
