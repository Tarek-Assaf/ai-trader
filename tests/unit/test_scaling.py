from __future__ import annotations
from trader.scaling.autoscaler import Action, Autoscaler, AutoscalerPolicy, QueueMetrics, Worker
from trader.scaling.queue import Priority, QueueTask, RedisTaskQueue

class FakeRedis:
    def __init__(self): self.added=[]; self.acks=[]; self.groups=[]
    def xgroup_create(self,*args,**kwargs): self.groups.append((args,kwargs))
    def xadd(self,name,fields,**kwargs): self.added.append((name,fields)); return str(len(self.added))
    def xreadgroup(self,*args,**kwargs): return []
    def xack(self,name,group,*ids): self.acks.extend(ids); return len(ids)

def test_queue_serializes_and_sheds_low_priority_first():
    task=QueueTask('a',{'type':'research'},Priority.P2,idempotency_key='a')
    restored=QueueTask.from_fields(task.fields())
    assert restored==task
    keep,dropped=RedisTaskQueue.shed([QueueTask('p0',{},Priority.P0),task,QueueTask('p3',{},Priority.P3)],overload=True)
    assert [x.priority for x in keep]==[Priority.P0]
    assert [x.priority for x in dropped]==[Priority.P3,Priority.P2]

def test_retry_dead_letters_after_limit():
    redis=FakeRedis(); queue=RedisTaskQueue(redis); task=QueueTask('a',{},Priority.P1,retries=1,max_retries=1)
    assert queue.retry_or_dead_letter('1',task,error='failed')=='dead-letter'
    assert redis.acks==['1']; assert redis.added[0][0]=='trader:tasks:dead'

def test_autoscaler_never_targets_core_and_respects_cap():
    class Api:
        def __init__(self): self.created=0; self.terminated=[]
        def create_worker(self,**kwargs): self.created+=1; return 'new'
        def terminate_worker(self,pod_id): self.terminated.append(pod_id)
        def list_workers(self): return []
    api=Api(); policy=AutoscalerPolicy(enabled=True,max_workers=2,max_hourly_spend_usd=2)
    scaler=Autoscaler(api,policy)
    workers=[Worker('core',role='core',tags=frozenset({'core'})),Worker('w1')]
    actions=scaler.evaluate(QueueMetrics(depth=30,oldest_task_age_seconds=180,pressure_since=0),workers,now=180)
    assert actions[0].action is Action.CREATE
    scaler.apply(actions); assert api.created==1
    spend=scaler.evaluate(QueueMetrics(hourly_spend_usd=2.0),workers)
    assert all(a.action is Action.TERMINATE and a.pod_id!='core' for a in spend)

def test_worker_tagged_singletons_are_not_scalable():
    assert not Worker('core',tags=frozenset({'core'})).is_scalable()
    assert not Worker('w',tags=frozenset({'execution_service'})).is_scalable()
