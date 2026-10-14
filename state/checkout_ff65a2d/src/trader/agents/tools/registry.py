"""Read-only tool allow-list validation and dispatch."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

EXPECTED: dict[str, frozenset[str]] = {
    "Orchestrator": frozenset(),
    "Researcher": frozenset({"web_search", "fetch_url", "news_feed", "calendar", "macro_series"}),
    "Technical Analyst": frozenset({"get_snapshot", "get_candles", "run_indicator"}),
    "News and Macro Analyst": frozenset({"news_feed", "calendar", "macro_series", "fetch_url"}),
    "Strategist": frozenset({"get_snapshot", "query_memory", "quick_backtest"}),
    "Critic": frozenset({"query_memory", "calendar"}),
    "Performance Analyst": frozenset({"query_trades"}),
    "Reporter": frozenset({"query_trades", "make_chart"}),
}


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    handler: Any


class ToolRegistry:
    """LLM outputs are never interpreted as calls; trusted code may call read tools."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        try:
            tools = {str(k): str(v["permission"]) for k, v in config["tools"].items()}
            agents = {str(k): frozenset(str(x) for x in v) for k, v in config["agents"].items()}
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid tool configuration") from None
        if agents != EXPECTED:
            raise ValueError("tool allow-list differs from reviewed permission table")
        if any(permission != "read" for permission in tools.values()):
            raise ValueError("state-changing tools are not registrable")
        if any(name not in tools for names in agents.values() for name in names):
            raise ValueError("agent permission references an unknown tool")
        self._agents, self._permissions = agents, tools
        self._handlers: dict[str, Any] = {}

    @classmethod
    def from_yaml(cls, path: str = "config/tools.yaml") -> ToolRegistry:
        try:
            with open(path, encoding="utf-8") as stream:
                config = yaml.safe_load(stream)
        except (OSError, yaml.YAMLError):
            raise ValueError("tool config unavailable or invalid") from None
        if not isinstance(config, dict):
            raise ValueError("tool config must be a mapping")
        return cls(config)

    def register(self, name: str, handler: Any, *, permission: str = "read") -> None:
        if self._permissions.get(name) != "read" or permission != "read":
            raise PermissionError("only configured read-only tools may be registered")
        if not callable(handler):
            raise ValueError("tool handler must be callable")
        self._handlers[name] = handler

    def call(self, agent: str, name: str, **arguments: Any) -> Any:
        if name not in self._agents.get(agent, frozenset()):
            raise PermissionError("tool is not allow-listed for this agent")
        if name not in self._handlers:
            raise LookupError("read-only tool handler is unavailable")
        return self._handlers[name](**arguments)
