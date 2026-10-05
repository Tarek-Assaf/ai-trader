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
