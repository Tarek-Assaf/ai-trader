"""Offline safety tests for the deterministic SettingsService."""

from __future__ import annotations

import base64
import hashlib
import hmac
import shutil
import struct
from pathlib import Path

import pytest
import yaml

from trader.control.settings_service import (
    AppliedChange,
    SettingsChangeRequest,
    SettingsRejected,
    SettingsService,
)
from trader.core.audit import AuditEvent
from trader.core.config import load_all_configs

OTP_SEED = "GEZDGNBVGY3TQOJQGE3TQOJQGEZDGNBVGY3TQOJQ"


def totp_at(now: float) -> str:
    key = base64.b32decode(OTP_SEED)
    counter = int(now // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.events.append(event)


class FakeVersions:
    def __init__(self) -> None:
        self.changes: list[AppliedChange] = []

    def append(self, change: AppliedChange) -> None:
        self.changes.append(change)


class FailingVersions:
    def append(self, change: AppliedChange) -> None:
        raise OSError("version store unavailable")


def make_service(values: dict[str, object], now: list[float], **kwargs: object) -> SettingsService:
    ceilings = {
        "max": {"risk_per_trade_pct": 1.0, "max_daily_loss_pct": 4.0},
        "min": {"min_reward_risk": 1.0},
        "strategy_lab_floors": {"gate1_min_trades": 200},
    }
    kwargs.setdefault("halt_active", lambda: False)
    return SettingsService(
        values,
        ceilings,
        clock=lambda: now[0],
        **kwargs,
    )


def test_tightening_applies_immediately_and_records(tmp_path):
    now = [100.0]
    audit = FakeAudit()
    versions = FakeVersions()
    notified: list[str] = []
    values: dict[str, object] = {"risk_per_trade_pct": 0.5}
    service = make_service(
        values,
        now,
        audit=audit,
        notify=notified.append,
        version_store=versions,
        config_path=tmp_path / "risk.yaml",
    )

    pending = service.request_change(SettingsChangeRequest("risk_per_trade_pct", 0.25))
    applied = service.confirm(pending)

    assert applied.direction == "tightening"
    assert values["risk_per_trade_pct"] == 0.25
    assert (tmp_path / "risk.yaml").exists()
    assert len(versions.changes) == 1
    assert [event.event_type for event in audit.events] == [
        "settings_change_requested",
        "settings_change_applied",
    ]
    assert len(notified) == 2


def test_bounds_and_non_settable_keys_are_refused():
    now = [100.0]
    values: dict[str, object] = {"risk_per_trade_pct": 0.5, "LIVE_TRADING_ENABLED": False}
    service = make_service(values, now)
    with pytest.raises(SettingsRejected, match="ceiling"):
        service.request_change("risk_per_trade_pct", 1.01)
    with pytest.raises(SettingsRejected, match="not changeable"):
        service.request_change("LIVE_TRADING_ENABLED", True)


def test_strategy_lab_floor_is_enforced():
    now = [100.0]
    service = make_service({"gate1_min_trades": 300}, now)
    with pytest.raises(SettingsRejected, match="floor"):
        service.request_change("gate1_min_trades", 199)


def test_every_settable_key_has_a_real_baked_bound():
    config = yaml.safe_load(
        (Path(__file__).parents[2] / "config" / "risk_ceilings.yaml").read_text(encoding="utf-8")
    )
    bounded = (
        set(config.get("max", {}))
        | set(config.get("min", {}))
        | set(config.get("strategy_lab_floors", {}))
    )
    assert SettingsService._SETTABLE_KEYS <= bounded


def test_loosening_requires_totp_and_is_delayed_and_cancellable():
    now = [120.0]
    values: dict[str, object] = {"risk_per_trade_pct": 0.5}
    service = make_service(values, now, totp_secret=OTP_SEED, cooldown_seconds=60)
    pending = service.request_change("risk_per_trade_pct", 0.75)
    applied = service.confirm(pending, totp_code=totp_at(now[0]))
    assert applied.direction == "loosening"
    assert values["risk_per_trade_pct"] == 0.5
    assert service.apply_due() == ()
    now[0] += 60
    assert service.apply_due() == (applied,)
    assert values["risk_per_trade_pct"] == 0.75

    pending = service.request_change("risk_per_trade_pct", 0.8)
    assert service.cancel(pending)
    with pytest.raises(SettingsRejected, match="expired or invalid"):
        service.confirm(pending, totp_code=totp_at(now[0]))


def test_loosening_totp_lockout_replay_and_halt():
    now = [120.0]
    service = make_service({"risk_per_trade_pct": 0.5}, now, totp_secret=OTP_SEED)
    with pytest.raises(SettingsRejected, match="halt"):
        service.request_change("risk_per_trade_pct", 0.75, halt_active=True)

    pending = service.request_change("risk_per_trade_pct", 0.75)
    for _ in range(3):
        with pytest.raises(SettingsRejected, match="TOTP"):
            service.confirm(pending, totp_code="000000")
    with pytest.raises(SettingsRejected, match="lockout"):
        service.confirm(pending, totp_code=totp_at(now[0]))

    # A pending confirmation still expires after five minutes, even after lockout.
    now[0] += 3602
    with pytest.raises(SettingsRejected, match="expired or invalid"):
        service.confirm(pending, totp_code=totp_at(now[0]))


def test_loosening_without_totp_and_inconsistent_loss_limits_are_refused():
    now = [100.0]
    service = make_service(
        {"risk_per_trade_pct": 0.5, "max_daily_loss_pct": 2.0, "max_weekly_loss_pct": 2.0},
        now,
    )
    with pytest.raises(SettingsRejected, match="configured TOTP"):
        service.request_change("risk_per_trade_pct", 0.75)
    with pytest.raises(SettingsRejected, match="weekly"):
        service.request_change("max_weekly_loss_pct", 1.0)
    with pytest.raises(SettingsRejected, match="weekly"):
        service.request_change("max_daily_loss_pct", 3.0)


def test_default_loosening_waits_for_next_day_and_halt_is_checked_again():
    now = [23 * 3600 + 59 * 60]
    halted = [False]
    service = make_service(
        {"risk_per_trade_pct": 0.5},
        now,
        totp_secret=OTP_SEED,
        halt_active=lambda: halted[0],
    )
    pending = service.request_change("risk_per_trade_pct", 0.75)
    halted[0] = True
    with pytest.raises(SettingsRejected, match="halt"):
        service.confirm(pending, totp_code=totp_at(now[0]))

    halted[0] = False
    assert service.cancel(pending)
    pending = service.request_change("risk_per_trade_pct", 0.75)
    applied = service.confirm(pending, totp_code=totp_at(now[0]))
    assert applied.effective_at == 24 * 3600
    assert service.apply_due() == ()


def test_revalidates_current_value_and_rejects_duplicate_or_unbounded_keys():
    now = [100.0]
    values: dict[str, object] = {"risk_per_trade_pct": 0.5}
    service = make_service(values, now, totp_secret=OTP_SEED, cooldown_seconds=60)
    pending = service.request_change("risk_per_trade_pct", 0.75)
    with pytest.raises(SettingsRejected, match="already pending"):
        service.request_change("risk_per_trade_pct", 0.8)
    values["risk_per_trade_pct"] = 0.9
    applied = service.confirm(pending)
    assert applied.direction == "tightening"
    assert values["risk_per_trade_pct"] == 0.75

    values["unbounded"] = 1
    with pytest.raises(SettingsRejected, match="not changeable"):
        service.request_change("unbounded", 2)
    service._values["max_effective_leverage"] = 5
    with pytest.raises(SettingsRejected, match="no baked"):
        service.request_change("max_effective_leverage", 4)


def test_positive_and_integer_validation_and_transactional_persistence(tmp_path):
    now = [100.0]
    values: dict[str, object] = {"risk_per_trade_pct": 0.5, "max_orders_per_hour": 100}
    service = make_service(values, now, config_path=tmp_path / "risk.yaml")
    for invalid in (0, -1):
        with pytest.raises(SettingsRejected, match="greater"):
            service.request_change("risk_per_trade_pct", invalid)
    with pytest.raises(SettingsRejected, match="whole"):
        service.request_change("max_orders_per_hour", 100.5)

    # Decimal values are normalized before YAML serialization.
    service = make_service(values, now, config_path=tmp_path / "missing" / "risk.yaml")
    pending = service.request_change("risk_per_trade_pct", 0.25)
    assert service.confirm(pending)
    assert values["risk_per_trade_pct"] == 0.25


def test_config_dir_preserves_nested_strategy_schema_and_defaults(tmp_path):
    root = Path(__file__).parents[2]
    volume = tmp_path / "config"
    volume.mkdir()
    for name in ("risk.yaml", "strategy_lab.yaml"):
        shutil.copy(root / "config" / name, volume / name)
    now = [100.0]
    service = SettingsService(
        {"risk_per_trade_pct": 0.5, "gate1_min_trades": 300},
        yaml.safe_load((root / "config" / "risk_ceilings.yaml").read_text(encoding="utf-8")),
        clock=lambda: now[0],
        config_dir=volume,
        halt_active=lambda: False,
    )
    service.confirm(service.request_change("risk_per_trade_pct", 0.25))
    gate = service.request_change("gate1_min_trades", 350)
    service.confirm(gate)
    loaded = load_all_configs(root / "config", volume_config_dir=volume)
    assert loaded["risk"].risk_per_trade_pct == 0.25
    assert loaded["strategy_lab"].gate1_backtest_to_paper.min_trades == 350


def test_scheduled_change_cannot_overwrite_a_newer_current_value():
    now = [100.0]
    values: dict[str, object] = {"risk_per_trade_pct": 0.5}
    service = make_service(values, now, totp_secret=OTP_SEED, cooldown_seconds=60)
    pending = service.request_change("risk_per_trade_pct", 0.75)
    applied = service.confirm(pending, totp_code=totp_at(now[0]))
    values["risk_per_trade_pct"] = 0.25
    now[0] += 60
    assert service.apply_due() == ()
    assert values["risk_per_trade_pct"] == 0.25
    assert applied.change_id not in service._scheduled


def test_version_store_failure_keeps_memory_and_file_unchanged(tmp_path):
    path = tmp_path / "risk.yaml"
    path.write_text("risk_per_trade_pct: 0.5\nother: 1\n", encoding="utf-8")
    now = [100.0]
    values: dict[str, object] = {"risk_per_trade_pct": 0.5}
    service = make_service(values, now, config_path=path, version_store=FailingVersions())
    pending = service.request_change("risk_per_trade_pct", 0.25)
    with pytest.raises(SettingsRejected, match="persisted"):
        service.confirm(pending)
    assert values["risk_per_trade_pct"] == 0.5
    assert path.read_text(encoding="utf-8") == "risk_per_trade_pct: 0.5\nother: 1\n"
