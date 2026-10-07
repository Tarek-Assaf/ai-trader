from __future__ import annotations

from types import SimpleNamespace

from trader.scaling.autoscaler import Worker
from trader.scaling.runtime import CoreAutoscaler, build_core_autoscaler


class Redis:
    def xlen(self, _stream: str) -> int:
        return 0

    def xrange(self, _stream: str, **_kwargs: object) -> list[object]:
        return []


class Api:
    def __init__(self) -> None:
        self.workers = [Worker("core", role="core", tags=frozenset({"core"}))]

    def list_workers(self) -> list[Worker]:
        return self.workers

    def create_worker(self, *, tags: dict[str, str]) -> str:
        assert tags["managed_by"] == "ai-trader-autoscaler"
        return "worker-1"

    def terminate_worker(self, pod_id: str) -> None:
        del pod_id


def test_disabled_scaling_does_not_require_runpod_secret() -> None:
    settings = SimpleNamespace(role="core", runpod_api_key=None)
    configs = {"scaling": {"enabled": False}}
    assert build_core_autoscaler(settings, configs, Redis()) is None


def test_worker_role_never_constructs_core_autoscaler() -> None:
    settings = SimpleNamespace(role="worker", runpod_api_key=None)
    configs = {"scaling": {"enabled": True}}
    assert build_core_autoscaler(settings, configs, Redis()) is None


def test_enabled_scaling_uses_policy_and_queue_from_core() -> None:
    settings = SimpleNamespace(role="core", runpod_api_key=None)
    configs = {
        "scaling": {
            "enabled": True,
            "min_workers": 0,
            "max_workers": 2,
            "max_hourly_spend_usd": 1.5,
            "scale_up_when": {
                "queue_depth_above": 5,
                "for_minutes": 0,
                "oldest_task_age_seconds_above": 10,
            },
            "scale_down_when": {"idle_minutes": 5},
        }
    }
    scaler = build_core_autoscaler(settings, configs, Redis(), api=Api())
    assert isinstance(scaler, CoreAutoscaler)
    assert scaler.autoscaler.policy.max_workers == 2
    assert scaler.reconcile_orphans() == []
