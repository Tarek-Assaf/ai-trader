"""Minimal RunPod worker API adapter with strict response validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import httpx

from trader.scaling.autoscaler import Worker


class HttpClient(Protocol):
    def get(self, url: str, **kwargs: Any) -> httpx.Response: ...
    def post(self, url: str, **kwargs: Any) -> httpx.Response: ...
    def delete(self, url: str, **kwargs: Any) -> httpx.Response: ...


class RunPodApiError(RuntimeError):
    """A validated, redacted RunPod API failure."""


class RunPodClient:
    def __init__(
        self,
        api_key: str,
        *,
        image: str = "",
        environment: Mapping[str, str] | None = None,
        endpoint: str = "https://api.runpod.io/v2",
        http: HttpClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("RunPod API key is required")
        self._http = http or httpx.Client(timeout=15.0)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._image = image
        self._environment = dict(environment or {})
        self._endpoint = endpoint.rstrip("/")

    def _check(self, response: httpx.Response) -> Mapping[str, Any]:
        if response.status_code < 200 or response.status_code >= 300:
            raise RunPodApiError(f"RunPod request failed with status {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RunPodApiError("RunPod returned invalid JSON") from exc
        if not isinstance(data, Mapping):
            raise RunPodApiError("RunPod returned an invalid object")
        return data

    @staticmethod
    def _tags(value: Any) -> frozenset[str]:
        if isinstance(value, Mapping):
            return frozenset(f"{key}={item}" for key, item in value.items())
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            return frozenset(str(item) for item in value)
        return frozenset()

    @classmethod
    def _worker(cls, data: Mapping[str, Any]) -> Worker:
        pod_id = data.get("id") or data.get("podId")
        if not isinstance(pod_id, str) or not pod_id:
            raise RunPodApiError("RunPod response omitted pod id")
        env = data.get("env")
        role = data.get("role")
        if role is None and isinstance(env, Mapping):
            role = env.get("ROLE")
        return Worker(
            pod_id=pod_id,
            role=str(role or "unknown"),
            status=str(data.get("status", data.get("desiredStatus", "unknown"))),
            hourly_cost_usd=float(data.get("costPerHr", data.get("hourly_cost_usd", 0.0)) or 0.0),
            tags=cls._tags(data.get("tags", ())),
            cpu_percent=float(data.get("cpuPercent", data.get("cpu_percent", 0.0)) or 0.0),
        )

    def list_workers(self) -> list[Worker]:
        data = self._check(
            self._http.get(
                f"{self._endpoint}/pods",
                headers=self._headers,
                params={"status": "RUNNING"},
            )
        )
        rows = data.get("pods", data.get("data", []))
        if not isinstance(rows, list):
            raise RunPodApiError("RunPod pods response omitted list")
        return [self._worker(row) for row in rows if isinstance(row, Mapping)]

    def create_worker(self, *, tags: dict[str, str]) -> str:
        if not self._image:
            raise ValueError("RunPod worker image is required")
        payload = {
            "imageName": self._image,
            "env": self._environment,
            "dockerArgs": "",
            "name": "ai-trader-worker",
            "cloudType": "SECURE",
            "tags": [f"{key}={value}" for key, value in tags.items()],
        }
        data = self._check(
            self._http.post(
                f"{self._endpoint}/pods",
                headers=self._headers,
                json=payload,
            )
        )
        return self._worker(data).pod_id

    def terminate_worker(self, pod_id: str) -> None:
        if not pod_id or "/" in pod_id:
            raise ValueError("invalid pod id")
        self._check(
            self._http.delete(
                f"{self._endpoint}/pods/{pod_id}",
                headers=self._headers,
            )
        )
