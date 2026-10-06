"""Worker process loop for queue tasks; trading singletons stay on core."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

from trader.scaling.queue import RedisTaskQueue

SINGLETON_TASKS = frozenset(
    {
        "risk_guardian",
        "execution_service",
        "decision_loop",
        "position_management",
        "risk_checks",
    }
)
Handler = Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True, slots=True)
class WorkerResult:
    task_id: str
    status: str
    error: str = ""


class WorkerRunner:
    def __init__(
        self,
        queue: RedisTaskQueue,
        *,
        consumer: str,
        role: str = "worker",
        handlers: Mapping[str, Handler] | None = None,
    ) -> None:
        if role != "worker":
            raise ValueError("WorkerRunner requires ROLE=worker")
        self.queue = queue
        self.consumer = consumer
        self.handlers = dict(handlers or {})
        self._stop = False

    def register(self, name: str, handler: Handler) -> None:
        if name in SINGLETON_TASKS:
            raise ValueError("singleton handlers run on core only")
        self.handlers[name] = handler

    def run_once(self, *, count: int = 1) -> list[WorkerResult]:
        results: list[WorkerResult] = []
        for message_ref, task in self.queue.claim(self.consumer, count=count):
            name = str(task.payload.get("type", task.payload.get("task_type", "")))
            handler = self.handlers.get(name)
            if handler is None or name in SINGLETON_TASKS:
                self.queue.retry_or_dead_letter(
                    message_ref,
                    task,
                    error="unsupported worker task",
                )
                results.append(WorkerResult(task.task_id, "rejected", "unsupported worker task"))
                continue

            pool = ThreadPoolExecutor(max_workers=1)
            future: Future[None] = pool.submit(handler, task.payload)
            try:
                future.result(timeout=task.timeout_seconds)
            except FutureTimeout:
                future.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
                self.queue.retry_or_dead_letter(
                    message_ref,
                    task,
                    error="worker timeout",
                )
                results.append(WorkerResult(task.task_id, "timeout", "worker timeout"))
            except Exception as exc:
                pool.shutdown(wait=False, cancel_futures=True)
                self.queue.retry_or_dead_letter(
                    message_ref,
                    task,
                    error=f"{type(exc).__name__}: {exc}",
                )
                results.append(WorkerResult(task.task_id, "failed", type(exc).__name__))
            else:
                pool.shutdown(wait=True, cancel_futures=True)
                self.queue.acknowledge(message_ref)
                results.append(WorkerResult(task.task_id, "completed"))
        return results

    def run_forever(self, *, poll_seconds: float = 1.0) -> None:
        while not self._stop:
            self.run_once()
            if self._stop:
                break
            time.sleep(max(0.0, poll_seconds))

    def stop(self) -> None:
        self._stop = True
