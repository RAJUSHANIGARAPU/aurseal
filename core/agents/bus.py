"""Thread-safe in-process pub/sub message bus for inter-agent communication."""

from __future__ import annotations

import threading
from collections.abc import Callable


class AgentBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: dict[str, list[Callable[[dict], None]]] = {}
        self._history: dict[str, list[dict]] = {}

    def subscribe(self, topic: str, handler: Callable[[dict], None]) -> None:
        with self._lock:
            self._handlers.setdefault(topic, []).append(handler)

    def publish(self, topic: str, payload: dict) -> None:
        with self._lock:
            self._history.setdefault(topic, []).append(payload)
            handlers = list(self._handlers.get(topic, []))
        for handler in handlers:
            handler(payload)

    def get_history(self, topic: str, n: int = 20) -> list[dict]:
        with self._lock:
            return self._history.get(topic, [])[-n:]

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()
            self._history.clear()


default_bus: AgentBus = AgentBus()
