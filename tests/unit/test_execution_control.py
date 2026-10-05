"""Independent control triggers and authenticated API actions."""

import base64
import hashlib
import hmac
import struct
import time

from fastapi.testclient import TestClient

from trader.api.app import app
from trader.control.totp import verify_totp
from trader.execution.control import (
    CompositeControlSource,
    ControlMode,
    ControlUnavailable,
    FlagFileControlSource,
    RedisControlSource,
)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.fail = False

    def get(self, key):
        if self.fail:
            raise OSError("connection unavailable")
        return self.values.get(key)

    def set(self, key, value):
        if self.fail:
            raise OSError("connection unavailable")
        self.values[key] = value

    def delete(self, key):
        if self.fail:
            raise OSError("connection unavailable")
        self.values.pop(key, None)


def test_composite_sources_prioritize_kill_and_fail_closed(tmp_path):
    flags = tmp_path / "control"
    flags.mkdir()
    redis = FakeRedis()
    writer = RedisControlSource(redis)
    source = CompositeControlSource((FlagFileControlSource(flags), writer))
    (flags / "pause.flag").touch()
    writer.set(ControlMode.KILL)
    assert source.read() is ControlMode.KILL
    (flags / "kill.flag").touch()
    writer.set(ControlMode.RUN)
    assert source.read() is ControlMode.KILL
    (flags / "kill.flag").unlink()
    (flags / "pause.flag").unlink()
    redis.fail = True
    try:
        source.read()
    except ControlUnavailable:
        pass
    else:
        raise AssertionError("a broken Redis control trigger must fail closed")


def test_rfc6238_totp_verifies_known_vector():
    totp_seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert verify_totp(totp_seed, "287082", now=59)
    assert not verify_totp(totp_seed, "287083", now=59)
    assert not verify_totp(totp_seed, "12345", now=59)


def _totp_now(secret: str) -> str:
    key = base64.b32decode(secret)
    counter = int(time.time() // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


def test_control_api_requires_token_and_second_factor(monkeypatch):
    owner_credential = "control-token-for-unit-test"
    totp_seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    monkeypatch.setenv("CONTROL_API_TOKEN", owner_credential)
    monkeypatch.setenv("OWNER_TOTP_SECRET", totp_seed)
    monkeypatch.setenv("ROLE", "worker")
    monkeypatch.setenv("REDIS_URL", "")
    redis = FakeRedis()
    writer = RedisControlSource(redis)

    with TestClient(app) as client:
        app.state.execution_control_writer = writer
        denied = client.post("/control/kill", json={"totp_code": _totp_now(totp_seed)})
        assert denied.status_code == 401
        denied = client.post(
            "/control/kill",
            headers={"X-Control-Token": owner_credential},
            json={"totp_code": "000000"},
        )
        assert denied.status_code == 401
        accepted = client.post(
            "/control/kill",
            headers={"X-Control-Token": owner_credential},
            json={"totp_code": _totp_now(totp_seed)},
        )
        assert accepted.status_code == 202
        assert redis.values[writer._key] == "kill"


def _totp_at(secret: str, now: float) -> str:
    key = base64.b32decode(secret)
    counter = int(now // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


def test_control_auth_rejects_replay_and_locks_out_then_expires():
    totp_seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    writer = RedisControlSource(FakeRedis())
    now = 120.0
    code = _totp_at(totp_seed, now)
    assert writer.authorize("token", "token", totp_seed, code, now=now)
    assert not writer.authorize("token", "token", totp_seed, code, now=now)
    for _ in range(4):
        assert not writer.authorize("token", "wrong", totp_seed, code, now=now)
    assert not writer.authorize("token", "token", totp_seed, code, now=now)
    later = now + 901
    assert writer.authorize("token", "token", totp_seed, _totp_at(totp_seed, later), now=later)


def test_redis_writer_never_downgrades_kill_to_pause():
    writer = RedisControlSource(FakeRedis())
    writer.set(ControlMode.KILL)
    writer.set(ControlMode.PAUSE)
    assert writer.read() is ControlMode.KILL
