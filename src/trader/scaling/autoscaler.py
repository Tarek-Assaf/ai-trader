"""Safe worker autoscaling policy for the core process."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, Sequence

SINGLETONS=frozenset({'risk_guardian','execution_service','decision_loop'})

class Action(StrEnum):
    CREATE='create'; TERMINATE='terminate'; NONE='none'

@dataclass(frozen=True, slots=True)
class QueueMetrics:
    depth:int=0
    oldest_task_age_seconds:float=0.0
    idle_minutes:float=0.0
    hourly_spend_usd:float=0.0
    pressure_since:float|None=None

@dataclass(frozen=True, slots=True)
class Worker:
    pod_id:str
    role:str='worker'
    status:str='running'
    hourly_cost_usd:float=0.0
    tags:frozenset[str]=field(default_factory=frozenset)
    cpu_percent:float=0.0
    def is_scalable(self)->bool:
        return self.role=='worker' and 'core' not in self.tags and not (SINGLETONS & self.tags)

@dataclass(frozen=True, slots=True)
class ScalingAction:
    action:Action
    pod_id:str|None=None
    reason:str=''

class RunPodWorkerApi(Protocol):
    def create_worker(self, *, tags:dict[str,str]) -> str: ...
    def terminate_worker(self, pod_id:str) -> None: ...
    def list_workers(self) -> Sequence[Worker]: ...

@dataclass(frozen=True, slots=True)
class AutoscalerPolicy:
    enabled:bool=False
    min_workers:int=0
    max_workers:int=3
    max_hourly_spend_usd:float=2.0
    queue_depth_above:int=20
    pressure_for_minutes:float=3.0
    oldest_task_age_seconds_above:float=120.0
    idle_minutes:float=15.0
    def __post_init__(self)->None:
        if self.min_workers<0 or self.max_workers<self.min_workers: raise ValueError('invalid worker bounds')
        if self.max_hourly_spend_usd<0: raise ValueError('hourly cap must be non-negative')

class Autoscaler:
    """Evaluate scaling without ever targeting core/singleton pods."""
    def __init__(self, api:RunPodWorkerApi, policy:AutoscalerPolicy)->None:
        self.api=api; self.policy=policy
    def evaluate(self, metrics:QueueMetrics, workers:Sequence[Worker], *, now:float|None=None)->list[ScalingAction]:
        p=self.policy
        if not p.enabled:return [ScalingAction(Action.NONE,reason='scaling disabled')]
        scalable=[w for w in workers if w.is_scalable() and w.status not in {'terminated','terminating'}]
        if metrics.hourly_spend_usd>=p.max_hourly_spend_usd:
            return [ScalingAction(Action.TERMINATE,w.pod_id,'hourly spend cap reached') for w in scalable]
        pressured=metrics.depth>p.queue_depth_above and metrics.oldest_task_age_seconds>p.oldest_task_age_seconds_above
        if pressured and len(scalable)<p.max_workers:
            since=metrics.pressure_since
            elapsed=(now-since)/60 if since is not None and now is not None else p.pressure_for_minutes
            if elapsed>=p.pressure_for_minutes:
                return [ScalingAction(Action.CREATE,reason='queue pressure')]
        if metrics.idle_minutes>=p.idle_minutes and len(scalable)>p.min_workers:
            victim=min(scalable,key=lambda w:(w.cpu_percent,w.hourly_cost_usd,w.pod_id))
            return [ScalingAction(Action.TERMINATE,victim.pod_id,'idle worker')]
        return [ScalingAction(Action.NONE,reason='within policy')]
    def apply(self, actions:Sequence[ScalingAction])->list[str]:
        created=[]
        for action in actions:
            if action.action is Action.CREATE:
                created.append(self.api.create_worker(tags={'role':'worker','managed_by':'ai-trader-autoscaler'}))
            elif action.action is Action.TERMINATE and action.pod_id:
                self.api.terminate_worker(action.pod_id)
        return created
    def reconcile_orphans(self, workers:Sequence[Worker])->list[str]:
        """Terminate unmanaged worker pods beyond the configured cap; never core."""
        if not self.policy.enabled:return []
        managed=[w for w in workers if w.is_scalable() and 'managed_by=ai-trader-autoscaler' in w.tags]
        extras=max(0,len(managed)-self.policy.max_workers)
        victims=sorted(managed,key=lambda w:(w.cpu_percent,w.pod_id))[:extras]
        for worker in victims:self.api.terminate_worker(worker.pod_id)
        return [w.pod_id for w in victims]
