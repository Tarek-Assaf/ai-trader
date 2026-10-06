"""Minimal RunPod worker API adapter with strict response validation."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence
import httpx

class HttpClient(Protocol):
    def get(self,url:str,**kwargs:Any)->httpx.Response: ...
    def post(self,url:str,**kwargs:Any)->httpx.Response: ...
    def delete(self,url:str,**kwargs:Any)->httpx.Response: ...

@dataclass(frozen=True, slots=True)
class PodInfo:
    pod_id:str
    status:str
    role:str
    hourly_cost_usd:float=0.0
    tags:frozenset[str]=frozenset()

class RunPodApiError(RuntimeError):
    """A validated, redacted RunPod API failure."""

class RunPodClient:
    def __init__(self,api_key:str,*,endpoint:str='https://api.runpod.io/v2',http:HttpClient|None=None)->None:
        if not api_key: raise ValueError('RunPod API key is required')
        self._http=http or httpx.Client(timeout=15.0)
        self._headers={'Authorization':f'Bearer {api_key}','Content-Type':'application/json'}
        self._endpoint=endpoint.rstrip('/')
    def _check(self,response:httpx.Response)->Any:
        if response.status_code<200 or response.status_code>=300: raise RunPodApiError(f'RunPod request failed with status {response.status_code}')
        try:data=response.json()
        except ValueError as exc: raise RunPodApiError('RunPod returned invalid JSON') from exc
        if not isinstance(data,dict): raise RunPodApiError('RunPod returned an invalid object')
        return data
    @staticmethod
    def _pod(data:Mapping[str,Any])->PodInfo:
        pod_id=data.get('id') or data.get('podId')
        if not isinstance(pod_id,str) or not pod_id: raise RunPodApiError('RunPod response omitted pod id')
        tags=data.get('tags',())
        if isinstance(tags,Mapping): tags=tags.keys()
        if isinstance(tags,(str,bytes)) or not isinstance(tags,Sequence): tags=()
        return PodInfo(pod_id,status=str(data.get('status','unknown')),role=str(data.get('role','worker')),hourly_cost_usd=float(data.get('costPerHr',data.get('hourly_cost_usd',0.0)) or 0.0),tags=frozenset(str(x) for x in tags))
    def list_workers(self)->list[PodInfo]:
        data=self._check(self._http.get(f'{self._endpoint}/pods',headers=self._headers,params={'status':'RUNNING'})); rows=data.get('pods',data.get('data',[]))
        if not isinstance(rows,list): raise RunPodApiError('RunPod pods response omitted list')
        return [self._pod(row) for row in rows if isinstance(row,Mapping) and str(row.get('role','worker'))=='worker']
    def create_worker(self,*,image:str,env:Mapping[str,str],tags:Mapping[str,str])->str:
        payload={'imageName':image,'env':dict(env),'dockerArgs':'','name':'ai-trader-worker','cloudType':'SECURE','tags':[f'{k}={v}' for k,v in tags.items()]}
        data=self._check(self._http.post(f'{self._endpoint}/pods',headers=self._headers,json=payload)); pod=self._pod(data); return pod.pod_id
    def terminate_worker(self,pod_id:str)->None:
        if not pod_id or '/' in pod_id: raise ValueError('invalid pod id')
        self._check(self._http.delete(f'{self._endpoint}/pods/{pod_id}',headers=self._headers))
