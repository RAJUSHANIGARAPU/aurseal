"""Test-data loader: a base JSON file per data set, deep-merged with a per-environment overlay.

``ui/testdata/<key>.json`` holds the canonical inputs; ``ui/testdata/<key>.<env>.json``
overrides only what differs on that environment (objects merge recursively;
scalars and arrays replace). Values are read by dotted path; :meth:`TestData.get`
fails fast on a missing key and names both files, so a typo never reads as
``None``. Parsed data is cached per (key, env).

The environment is the ``env`` argument, else ``TEST_ENV``, else ``qa`` — the
same default as the ``--env`` option.
"""

from __future__ import annotations

import copy
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from api.engine.context_memory import resolve_path

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "ui" / "testdata"
_MISSING = object()


def deep_merge(base: Any, overlay: Any) -> Any:
    """Objects merge recursively; anything else in ``overlay`` replaces ``base``."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = dict(base)
        for k, v in overlay.items():
            out[k] = deep_merge(base[k], v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(overlay)


class TestData:
    __test__ = False  # not a pytest test class

    def __init__(self, key: str, data: dict, base_file: Path, overlay_file: Path | None) -> None:
        self.key = key
        self.data = data
        self.base_file = base_file
        self.overlay_file = overlay_file

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        try:
            return copy.deepcopy(resolve_path(self.data, dotted))
        except (KeyError, IndexError, TypeError):
            if default is not _MISSING:
                return default
            overlay = self.overlay_file.name if self.overlay_file else "no overlay"
            raise KeyError(
                f"'{dotted}' not found in test data '{self.key}' "
                f"({self.base_file.name} + {overlay})"
            ) from None


def load(key: str, env: str | None = None, directory: str | Path | None = None) -> TestData:
    env = env or os.environ.get("TEST_ENV") or "qa"
    return _load(key, env, str(Path(directory) if directory else DEFAULT_DIR))


@lru_cache(maxsize=64)
def _load(key: str, env: str, directory: str) -> TestData:
    base = Path(directory) / f"{key}.json"
    if not base.is_file():
        raise FileNotFoundError(f"test data '{key}' not found: {base}")
    data = json.loads(base.read_text(encoding="utf-8"))
    overlay = Path(directory) / f"{key}.{env}.json"
    if overlay.is_file():
        data = deep_merge(data, json.loads(overlay.read_text(encoding="utf-8")))
        return TestData(key, data, base, overlay)
    return TestData(key, data, base, None)
