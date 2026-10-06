"""Worker process loop for queue tasks; trading singletons stay on core."""
from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from trader.scaling.queue import Priority, QueueTask, RedisTaskQueue

SINGLETON_TASKS=frozenset({'risk_guardian','execution_service','decision_loop','position_management','risk_checks'})
Handler=Callable[[Mapping[str,Any]],None]

@dataclass(frozen=True, slots=True)
class WorkerResult:
    task_id:str
    status:str
    error:str=''

class WorkerRunner:
    def __init__(self,queue:RedisTaskQueue,*,consumer:str,role:str='worker',handlers:Mapping[str,Handler]|None=None)->None:
        if role!='worker': raise ValueError('WorkerRunner requires ROLE=worker')
        self.queue=queue; self.consumer=consumer; self.handlers=dict(handlers or {}); self._stop=False
    def register(self,name:str,handler:Handler)->None:
        if name in SINGLETON_TASKS: raise ValueError('singleton handlers run on core only')
        self.handlers[name]=handler
    def run_once(self,*,count:int=1)->list[WorkerResult]:
        results=[]
        for message_id,task in self.queue.claim(self.consumer,count=count):
            name=str(task.payload.get('type',task.payload.get('task_type',''))); handler=self.handlers.get(name)
            if handler is None or name in SINGLETON_TASKS:
                self.queue.retry_or_dead_letter(message_id,task,error='unsupported worker task')
                results.append(WorkerResult(task.task_id,'rejected','unsupported worker task')); continue
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(handler,task.payload); future.result(timeout=task.timeout_seconds)
            except FutureTimeout:
                self.queue.retry_or_dead_letter(message_id,task,error='worker timeout'); results.append(WorkerResult(task.task_id,'timeout','worker timeout'))
            except Exception as exc:
                self.queue.retry_or_dead_letter(message_id,task,error=f'{type(exc).__name__}: {exc}'); results.append(WorkerResult(task.task_id,'failed',type(exc).__name__))
            else:
                self.queue.acknowledge(message_id); results.append(WorkerResult(task.task_id,'completed'))
        return results
    def run_forever(self,*,poll_seconds:float=1.0)->None:
        while not self._stop:
            self.run_once()
            if self._stop: break
            time.sleep(max(0.0,poll_seconds))
    def stop(self)->None:self._stop=True
