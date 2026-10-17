"""Redis Streams queue with priorities, idempotency, retries and a DLQ."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Protocol, cast


class Priority(IntEnum):
    """Scheduling priority; lower values are more important."""

    P0 = 0
    P1 = 1
    P2 = 2
    P3 = 3


class StreamClient(Protocol):
    def xadd(self, name: str, fields: Mapping[str, str], **kwargs: Any) -> str: ...

    def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Mapping[str, str],
        **kwargs: Any,
    ) -> Any: ...

    def xack(self, name: str, groupname: str, *ids: str) -> int: ...

    def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "0",
        mkstream: bool = False,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class QueueTask:
    """A serializable unit of work carried by a Redis stream."""

    task_id: str
    payload: Mapping[str, Any]
    priority: Priority = Priority.P2
    idempotency_key: str = ""
    timeout_seconds: int = 300
    retries: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.retries < 0 or self.max_retries < 0 or self.retries > self.max_retries:
            raise ValueError("invalid retry count")

    def fields(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "payload": json.dumps(dict(self.payload), sort_keys=True, separators=(",", ":")),
            "priority": str(int(self.priority)),
            "idempotency_key": self.idempotency_key or self.task_id,
            "timeout_seconds": str(self.timeout_seconds),
            "retries": str(self.retries),
            "max_retries": str(self.max_retries),
            "created_at": f"{self.created_at:.6f}",
        }

    @classmethod
    def from_fields(
        cls,
        fields: Mapping[str | bytes, str | bytes],
    ) -> QueueTask:
        def get(name: str, default: str = "") -> str:
            value = fields.get(name, fields.get(name.encode(), default))
            return value.decode() if isinstance(value, bytes) else str(value)

        return cls(
            task_id=get("task_id"),
            payload=cast(dict[str, Any], json.loads(get("payload", "{}"))),
            priority=Priority(int(get("priority", "2"))),
            idempotency_key=get("idempotency_key"),
            timeout_seconds=int(get("timeout_seconds", "300")),
            retries=int(get("retries", "0")),
            max_retries=int(get("max_retries", "3")),
            created_at=float(get("created_at", str(time.time()))),
        )


class RedisTaskQueue:
    """Priority-aware Redis Streams queue.

    Each priority has its own stream. Claiming walks P0 through P3, so a P3
    message cannot run while an older P0 message is available elsewhere.
    """

    def __init__(
        self,
        client: StreamClient,
        *,
        stream: str = "trader:tasks",
        group: str = "trader-workers",
        dead_letter: str = "trader:tasks:dead",
        maxlen: int = 100_000,
    ) -> None:
        self.client = client
        self.stream = stream
        self.group = group
        self.dead_letter = dead_letter
        self.maxlen = maxlen
        self._streams = {p: f"{stream}:p{int(p)}" for p in Priority}
        self._group_ready = False

    def _ensure_group(self) -> None:
        if self._group_ready:
            return
        for stream in self._streams.values():
            try:
                self.client.xgroup_create(stream, self.group, id="0", mkstream=True)
            except Exception as exc:
                if "BUSYGROUP" not in str(exc).upper():
                    raise
        self._group_ready = True

    def enqueue(self, task: QueueTask) -> str:
        """Enqueue once; duplicate idempotency keys return the existing marker."""
        marker = f"trader:task:idempotency:{task.idempotency_key or task.task_id}"
        setter = getattr(self.client, "set", None)
        if setter is not None and not setter(
            marker,
            task.task_id,
            nx=True,
            ex=task.timeout_seconds * 2,
        ):
            return task.task_id
        return str(
            self.client.xadd(
                self._streams[task.priority],
                task.fields(),
                maxlen=self.maxlen,
                approximate=True,
            )
        )

    def claim(
        self,
        consumer: str,
        *,
        count: int = 1,
        block_ms: int = 1000,
    ) -> list[tuple[str, QueueTask]]:
        self._ensure_group()
        claimed: list[tuple[str, QueueTask]] = []
        for priority in Priority:
            rows = self.client.xreadgroup(
                self.group,
                consumer,
                {self._streams[priority]: ">"},
                count=count - len(claimed),
                block=block_ms if not claimed else 0,
            )
            for _stream, messages in rows or []:
                for message_id, fields in messages:
                    mid = message_id.decode() if isinstance(message_id, bytes) else str(message_id)
                    ref = f"{self._streams[priority]}|{mid}"
                    claimed.append((ref, QueueTask.from_fields(fields)))
                    if len(claimed) >= count:
                        return claimed
        return claimed

    def _stream_and_id(self, message_ref: str, priority: Priority | None) -> tuple[str, str]:
        if "|" in message_ref:
            stream, message_id = message_ref.split("|", 1)
            return stream, message_id
        selected = self._streams[priority or Priority.P2]
        return selected, message_ref

    def acknowledge(self, message_ref: str, priority: Priority | None = None) -> int:
        stream, message_id = self._stream_and_id(message_ref, priority)
        return int(self.client.xack(stream, self.group, message_id))

    def retry_or_dead_letter(
        self,
        message_ref: str,
        task: QueueTask,
        *,
        error: str,
    ) -> str:
        self.acknowledge(message_ref, task.priority)
        if task.retries < task.max_retries:
            retry = QueueTask(
                task_id=task.task_id,
                payload={**task.payload, "last_error": error[:500]},
                priority=task.priority,
                idempotency_key=f"{task.idempotency_key or task.task_id}:retry:{task.retries + 1}",
                timeout_seconds=task.timeout_seconds,
                retries=task.retries + 1,
                max_retries=task.max_retries,
                created_at=task.created_at,
            )
            return self.enqueue(retry)
        self.client.xadd(
            self.dead_letter,
            {**task.fields(), "error": error[:500]},
            maxlen=self.maxlen,
            approximate=True,
        )
        return "dead-letter"

    @staticmethod
    def shed(
        tasks: list[QueueTask],
        *,
        overload: bool,
    ) -> tuple[list[QueueTask], list[QueueTask]]:
        if not overload:
            return tasks, []
        keep = [task for task in tasks if task.priority <= Priority.P1]
        dropped = [task for task in tasks if task.priority > Priority.P1]
        return keep, sorted(dropped, key=lambda task: task.priority, reverse=True)

    def depth(self) -> int:
        xlen = getattr(self.client, "xlen", None)
        if xlen is None:
            return 0
        return sum(int(xlen(stream)) for stream in self._streams.values())

    def oldest_task_age(self, *, now: float | None = None) -> float:
        xrange = getattr(self.client, "xrange", None)
        if xrange is None:
            return 0.0
        ages: list[float] = []
        for stream in self._streams.values():
            rows = xrange(stream, count=1)
            if rows:
                ages.append(QueueTask.from_fields(rows[0][1]).created_at)
        if not ages:
            return 0.0
        current = time.time() if now is None else now
        return max(0.0, current - min(ages))
