"""Global singleton tool registry — lets agents discover shared tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ToolRegistry:
    _instance: ToolRegistry | None = None
    _tools: dict[str, tuple[Callable[..., Any], dict]]

    def __init__(self) -> None:
        self._tools = {}

    @classmethod
    def get(cls) -> ToolRegistry:
        if cls._instance is None:
            cls._instance = ToolRegistry()
        return cls._instance

    def register(self, name: str, fn: Callable[..., Any], schema: dict) -> None:
        self._tools[name] = (fn, schema)

    def lookup(self, name: str) -> tuple[Callable[..., Any], dict] | None:
        return self._tools.get(name)

    def all_schemas(self) -> list[dict]:
        return [schema for _, schema in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())
