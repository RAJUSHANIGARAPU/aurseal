"""Thread-safe key-value store with JSONPath-like response extraction."""

import threading
from typing import Any


class ContextMemory:
    """Thread-safe in-memory store for sharing values across agent steps."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: Any) -> None:
        """Store a value under key."""
        with self._lock:
            self._store[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value by key, returning default if absent."""
        with self._lock:
            return self._store.get(key, default)

    def extract_from_response(self, response_json: dict, extractions: dict[str, str]) -> None:
        """Extract values from a JSON response using dotted-path expressions.

        Args:
            response_json: Parsed JSON response body.
            extractions: Mapping of {store_key: "dotted.path.expression"}.
                         Example: {"user_id": "data.id"} extracts response["data"]["id"].
        """
        for store_key, path in extractions.items():
            try:
                value = self._resolve_path(response_json, path)
                self.set(store_key, value)
            except (KeyError, TypeError, IndexError):
                pass

    def all(self) -> dict:
        """Return a snapshot of the entire store."""
        with self._lock:
            return dict(self._store)

    def clear(self) -> None:
        """Remove all stored values."""
        with self._lock:
            self._store.clear()

    @staticmethod
    def _resolve_path(obj: Any, path: str) -> Any:
        """Resolve a dotted key path against a nested dict/list structure."""
        parts = path.split(".")
        current = obj
        for part in parts:
            if isinstance(current, dict):
                current = current[part]
            elif isinstance(current, list):
                current = current[int(part)]
            else:
                raise TypeError(f"Cannot traverse into {type(current)} at path segment '{part}'")
        return current
