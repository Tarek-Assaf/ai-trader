from __future__ import annotations

import time
from typing import Any

import httpx

from trader.integrations.runpod.client import RunPodClient
from trader.scaling.autoscaler import (
    MANAGED_TAG,
    Action,
    Autoscaler,
    AutoscalerPolicy,
    QueueMetrics,
    Worker,
)
from trader.scaling.queue import Priority, QueueTask, RedisTaskQueue
from trader.workers.worker import WorkerRunner


class FakeRedis:
    def __init__(self) -> None:
        self.added: list[tuple[str, dict[str, str]]] = []
        self.acks: list[str] = []
        self.groups: list[str] = []
        self.reads: list[str] = []
        self._counter = 0

    def set(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def xgroup_create(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.groups.append(name)

    def xadd(self, name: str, fields: dict[str, str], **kwargs: Any) -> str:
        self._counter += 1
        self.added.append((name, fields))
        return str(self._counter)

    def xreadgroup(self, group: str, consumer: str, streams: dict[str, str], **kwargs: Any) -> list[Any]:
        self.reads.extend(streams)
        return []

    def xack(self, name: str, group: str, *ids: str) -> int:
        self.acks.extend(ids)
        return len(ids)

    def xlen(self, name: str) -> int:
        return sum(1 for stream, _ in self.added if stream == name)

    def xrange(self, name: str, **kwargs: Any) -> list[Any]:
        return []


def managed(pod_id: str, *, cpu: float = 10.0) -> Worker:
    return Worker(
        pod_id,
        role="worker",
        status="running",
        tags=frozenset({MANAGED_TAG}),
        cpu_percent=cpu,
    )


def test_queue_serializes_sheds_and_claims_global_priority() -> None:
    redis = FakeRedis()
    queue = RedisTaskQueue(redis)
    p3 = QueueTask("p3", {}, Priority.P3)
    p0 = QueueTask("p0", {}, Priority.P0)
    assert QueueTask.from_fields(p3.fields()) == p3
    queue.enqueue(p3)
    queue.enqueue(p0)
    assert redis.added[0][0].endswith(":p3")
    assert redis.added[1][0].endswith(":p0")
    queue.claim("consumer", count=1, block_ms=0)
    assert redis.reads[0].endswith(":p0")
    keep, dropped = RedisTaskQueue.shed(
        [p0, p3, QueueTask("p2", {}, Priority.P2)], overload=True
    )
    assert [task.priority for task in keep] == [Priority.P0]
    assert [task.priority for task in dropped] == [Priority.P3, Priority.P2]


def test_retry_dead_letters_after_limit() -> None:
    redis = FakeRedis()
    queue = RedisTaskQueue(redis)
    task = QueueTask("a", {}, Priority.P1, retries=1, max_retries=1)
    assert queue.retry_or_dead_letter("trader:tasks:p1|1", task, error="failed") == "dead-letter"
    assert redis.acks == ["1"]
    assert redis.added[0][0] == "trader:tasks:dead"


def test_autoscaler_default_denies_core_singleton_and_unknown_pods() -> None:
    class Api:
        def __init__(self) -> None:
            self.created = 0
            self.terminated: list[str] = []

        def create_worker(self, *, tags: dict[str, str]) -> str:
            self.created += 1
            assert tags["managed_by"] == "ai-trader-autoscaler"
            return "new"

        def terminate_worker(self, pod_id: str) -> None:
            self.terminated.append(pod_id)

        def list_workers(self) -> list[Worker]:
            return []

    api = Api()
    policy = AutoscalerPolicy(enabled=True, max_workers=2, max_hourly_spend_usd=2)
    scaler = Autoscaler(api, policy)
    workers = [
        Worker("core", role="core", tags=frozenset({"core"})),
        Worker("singleton", role="unknown", tags=frozenset({"execution_service"})),
        managed("w1"),
    ]
    pressure = scaler.evaluate(
        QueueMetrics(depth=30, oldest_task_age_seconds=180, pressure_since=0),
        workers,
        now=180,
    )
    assert pressure[0].action is Action.CREATE
    scaler.apply(pressure)
    assert api.created == 1
    spend = scaler.evaluate(QueueMetrics(hourly_spend_usd=2.0), workers)
    assert [action.pod_id for action in spend] == ["w1"]
    assert scaler.reconcile_orphans(workers) == []
    assert not Worker("core", tags=frozenset({"core"})).is_scalable()
    assert not Worker("unknown").is_scalable()


def test_load_rises_to_cap_then_scales_down_to_min() -> None:
    class Api:
        def __init__(self) -> None:
            self.created = 0
            self.terminated: list[str] = []

        def create_worker(self, *, tags: dict[str, str]) -> str:
            self.created += 1
            return f"w{self.created}"

        def terminate_worker(self, pod_id: str) -> None:
            self.terminated.append(pod_id)

        def list_workers(self) -> list[Worker]:
            return []

    api = Api()
    scaler = Autoscaler(
        api,
        AutoscalerPolicy(
            enabled=True,
            min_workers=1,
            max_workers=3,
            queue_depth_above=5,
            oldest_task_age_seconds_above=10,
            pressure_for_minutes=0,
            idle_minutes=1,
        ),
    )
    workers = [managed("w0")]
    for _ in range(2):
        action = scaler.evaluate(
            QueueMetrics(depth=20, oldest_task_age_seconds=30, pressure_since=0),
            workers,
            now=1,
        )
        assert action[0].action is Action.CREATE
        new_ids = scaler.apply(action)
        workers.extend(managed(pod_id) for pod_id in new_ids)
    assert len(workers) == 3
    while len(workers) > 1:
        action = scaler.evaluate(QueueMetrics(idle_minutes=2), workers)
        assert action[0].action is Action.TERMINATE
        scaler.apply(action)
        workers = [worker for worker in workers if worker.pod_id != action[0].pod_id]
    assert len(workers) == 1
    assert api.terminated == ["w0", "w1"]


def test_runpod_adapter_matches_autoscaler_protocol() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/pods":
            return httpx.Response(
                200,
                json={
                    "pods": [
                        {
                            "id": "pod-1",
                            "desiredStatus": "RUNNING",
                            "status": "RUNNING",
                            "role": "worker",
                            "tags": [MANAGED_TAG],
                            "costPerHr": 0.2,
                            "cpuPercent": 4,
                        }
                    ]
                },
            )
        raise AssertionError(request.url)

    client = RunPodClient(
        api_key="test-key",
        image="example/image:latest",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    workers = client.list_workers()
    assert workers[0].is_scalable()
    assert workers[0].cpu_percent == 4


def test_worker_timeout_does_not_wait_for_handler() -> None:
    class Queue:
        def claim(self, consumer: str, *, count: int = 1) -> list[tuple[str, QueueTask]]:
            return [("trader:tasks:p2|1", QueueTask("t", {"type": "research"}, timeout_seconds=1))]

        def retry_or_dead_letter(self, message_ref: str, task: QueueTask, *, error: str) -> str:
            return "retry"

        def acknowledge(self, message_ref: str) -> int:
            return 1

    def slow(_: Any) -> None:
        time.sleep(0.1)

    started = time.perf_counter()
    results = WorkerRunner(Queue(), consumer="c", handlers={"research": slow}).run_once()
    elapsed = time.perf_counter() - started
    assert results[0].status == "timeout"
    assert elapsed < 0.08
