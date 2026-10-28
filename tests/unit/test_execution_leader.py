"""Lease ownership changes and Redis failures always fail closed."""

import pytest

from trader.execution.leader import LeaseLost, RedisLeaderLease


class Redis:
    def __init__(self):
        self.value = None
        self.fail = False

    def set(self, name, value, *, nx, px):
        if self.fail:
            raise OSError("offline")
        if self.value is not None:
            return None
        self.value = value
        return True

    def eval(self, script, numkeys, *args):
        if self.fail:
            raise OSError("offline")
        if self.value != args[1]:
            return 0
        if "'del'" in script:
            self.value = None
        return 1


def test_only_one_leader_and_expired_owner_cannot_delete_successor():
    redis = Redis()
    first = RedisLeaderLease(redis, "execution")
    second = RedisLeaderLease(redis, "execution")
    first.acquire()
    with pytest.raises(LeaseLost):
        second.acquire()
    redis.value = None  # expiry
    second.acquire()
    first.release()
    second.check()


def test_redis_failure_and_changed_ownership_revoke_lease():
    redis = Redis()
    lease = RedisLeaderLease(redis, "guardian")
    lease.acquire()
    redis.value = "successor"
    with pytest.raises(LeaseLost):
        lease.check()
    with pytest.raises(LeaseLost):
        lease.check()
    redis.value = None
    redis.fail = True
    with pytest.raises(LeaseLost):
        lease.acquire()


class FlakyRedis(Redis):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def eval(self, script, numkeys, *args):
        if self.failures > 0:
            self.failures -= 1
            raise OSError("stalled")
        return super().eval(script, numkeys, *args)


def test_transient_redis_errors_do_not_cost_the_lease():
    redis = FlakyRedis(failures=3)
    pauses = []
    lease = RedisLeaderLease(redis, "execution", retries=5, sleep=pauses.append)
    lease.acquire()
    lease.check()  # three stalls, then success
    assert len(pauses) == 3
    lease.check()


def test_a_long_redis_outage_still_revokes_the_lease():
    redis = FlakyRedis(failures=99)
    lease = RedisLeaderLease(redis, "execution", retries=2, sleep=lambda _s: None)
    lease.acquire()
    with pytest.raises(LeaseLost):
        lease.check()


def test_lapsed_key_nobody_took_is_retaken_but_a_successor_is_respected():
    redis = Redis()
    lease = RedisLeaderLease(redis, "execution", retake_when_expired=True)
    lease.acquire()
    redis.value = None  # key lapsed during a stall
    lease.check()
    assert redis.value is not None
    redis.value = "successor"
    with pytest.raises(LeaseLost):
        lease.check()


def test_default_lease_behaviour_is_unchanged():
    redis = Redis()
    lease = RedisLeaderLease(redis, "execution")
    lease.acquire()
    redis.value = None
    with pytest.raises(LeaseLost):
        lease.check()
