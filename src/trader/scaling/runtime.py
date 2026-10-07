"""Core-pod wiring for the optional worker autoscaler."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from typing import Any

from trader.scaling.autoscaler import (
    Autoscaler,
    AutoscalerPolicy,
    QueueMetrics,
    RunPodWorkerApi,
    ScalingAction,
    Worker,
)
from trader.scaling.queue import RedisTaskQueue


class CoreAutoscaler:
    """Evaluate and apply worker scaling from the core process only."""

    def __init__(
        self,
        autoscaler: Autoscaler,
        queue: RedisTaskQueue,
        *,
        clock: Callable[[], float] = time.time,
        hourly_spend: Callable[[], float | None] | None = None,
    ) -> None:
        self.autoscaler = autoscaler
        self.queue = queue
        self._clock = clock
        self._hourly_spend = hourly_spend
        self._pressure_since: float | None = None

    def _workers(self) -> list[Worker]:
        return list(self.autoscaler.api.list_workers())

    def reconcile_orphans(self) -> list[str]:
        """Remove only tagged workers beyond the configured cap."""
        return self.autoscaler.reconcile_orphans(self._workers())

    def tick(self) -> list[ScalingAction]:
        """Evaluate one bounded scaling decision and apply its actions."""
        now = self._clock()
        workers = self._workers()
        depth = self.queue.depth()
        oldest = self.queue.oldest_task_age(now=now)
        pressured = (
            depth > self.autoscaler.policy.queue_depth_above
            and oldest > self.autoscaler.policy.oldest_task_age_seconds_above
        )
        if pressured:
            self._pressure_since = self._pressure_since or now
        else:
            self._pressure_since = None
        managed = [worker for worker in workers if worker.is_scalable()]
        if self._hourly_spend is None:
            hourly_spend = (
                None
                if any(worker.hourly_cost_usd is None for worker in managed)
                else sum(worker.hourly_cost_usd or 0.0 for worker in managed)
            )
        else:
            hourly_spend = self._hourly_spend()
        actions = self.autoscaler.evaluate(
            QueueMetrics(
                depth=depth,
                oldest_task_age_seconds=oldest,
                hourly_spend_usd=(None if hourly_spend is None else max(0.0, float(hourly_spend))),
                pressure_since=self._pressure_since,
            ),
            workers,
            now=now,
        )
        self.autoscaler.apply(actions)
        return actions


def _dump(value: object) -> Mapping[str, Any]:
    model_dump = getattr(value, "model_dump", None)
    raw = model_dump() if callable(model_dump) else value
    return raw if isinstance(raw, Mapping) else {}


def _number(values: Mapping[str, Any], key: str, default: float) -> float:
    try:
        return float(values.get(key, default))
    except (TypeError, ValueError):
        raise ValueError(f"scaling.{key} must be numeric") from None


def build_core_autoscaler(
    settings: object,
    configs: Mapping[str, object],
    redis_client: Any,
    *,
    api: RunPodWorkerApi | None = None,
) -> CoreAutoscaler | None:
    """Build scaling only for an eligible core pod when explicitly enabled."""
    values = _dump(configs.get("scaling"))
    if not bool(values.get("enabled", False)):
        return None
    role = str(getattr(settings, "role", ""))
    if role not in {"all", "core"}:
        return None
    if api is None:
        secret = getattr(settings, "runpod_api_key", None)
        api_key = secret.get_secret_value() if secret is not None else ""
        if not api_key:
            raise RuntimeError("scaling is enabled but RUNPOD_API_KEY is not configured")
        from trader.integrations.runpod.client import RunPodClient

        image = os.getenv("RUNPOD_WORKER_IMAGE", "").strip()
        if not image:
            raise RuntimeError("scaling is enabled but RUNPOD_WORKER_IMAGE is not configured")
        api = RunPodClient(
            api_key,
            image=image,
        )
    scale_up = _dump(values.get("scale_up_when"))
    scale_down = _dump(values.get("scale_down_when"))
    policy = AutoscalerPolicy(
        enabled=True,
        min_workers=int(_number(values, "min_workers", 0)),
        max_workers=int(_number(values, "max_workers", 3)),
        max_hourly_spend_usd=_number(values, "max_hourly_spend_usd", 2.0),
        queue_depth_above=int(_number(scale_up, "queue_depth_above", 20)),
        pressure_for_minutes=_number(scale_up, "for_minutes", 3.0),
        oldest_task_age_seconds_above=_number(scale_up, "oldest_task_age_seconds_above", 120.0),
        idle_minutes=_number(scale_down, "idle_minutes", 15.0),
    )
    if not all(callable(getattr(redis_client, name, None)) for name in ("xlen", "xrange")):
        raise RuntimeError("scaling requires a Redis task queue client")
    return CoreAutoscaler(Autoscaler(api, policy), RedisTaskQueue(redis_client))


__all__ = ["CoreAutoscaler", "build_core_autoscaler"]
