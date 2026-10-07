from __future__ import annotations

from types import SimpleNamespace

from trader.scaling.autoscaler import Action, Autoscaler, AutoscalerPolicy, ScalingAction, Worker
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


class Queue:
    def depth(self) -> int:
        return 30

    def oldest_task_age(self, *, now: float) -> float:
        del now
        return 180.0


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


def test_core_autoscaler_sums_managed_cost_and_blocks_unknown_cost() -> None:
    class CostApi(Api):
        def __init__(self, workers: list[Worker]) -> None:
            self.workers = workers
            self.terminated: list[str] = []

        def list_workers(self) -> list[Worker]:
            return self.workers

        def terminate_worker(self, pod_id: str) -> None:
            self.terminated.append(pod_id)

    policy = AutoscalerPolicy(
        enabled=True,
        max_workers=3,
        max_hourly_spend_usd=1.0,
        queue_depth_above=5,
        oldest_task_age_seconds_above=10,
        pressure_for_minutes=0,
    )
    workers = [
        Worker(
            "w1",
            role="worker",
            tags=frozenset({"managed_by=ai-trader-autoscaler"}),
            hourly_cost_usd=0.6,
        ),
        Worker(
            "w2",
            role="worker",
            tags=frozenset({"managed_by=ai-trader-autoscaler"}),
            hourly_cost_usd=0.5,
        ),
        Worker("core", role="core", tags=frozenset({"core"}), hourly_cost_usd=None),
    ]
    api = CostApi(workers)
    runtime = CoreAutoscaler(Autoscaler(api, policy), Queue(), clock=lambda: 180.0)
    actions = runtime.tick()
    assert actions == [
        ScalingAction(Action.TERMINATE, "w1", "hourly spend cap reached"),
        ScalingAction(Action.TERMINATE, "w2", "hourly spend cap reached"),
    ]
    workers[0] = Worker(
        "w1", role="worker", tags=frozenset({"managed_by=ai-trader-autoscaler"})
    )
    blocked = runtime.tick()
    assert blocked == [ScalingAction(Action.NONE, reason="hourly spend unknown; scaling blocked")]
