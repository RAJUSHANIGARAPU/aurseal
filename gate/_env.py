"""Defensive readers for the environment variables the gate is configured by.

A typo in a threshold must not abort a long run at its very end, after the
evidence exists and before it is written down, so every reader falls back to
its default instead of raising.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def _raw(name: str, env: Mapping[str, str] | None) -> str | None:
    value = (os.environ if env is None else env).get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def env_bool(name: str, default: bool, env: Mapping[str, str] | None = None) -> bool:
    raw = _raw(name, env)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return default


def env_int(name: str, default: int, env: Mapping[str, str] | None = None) -> int:
    raw = _raw(name, env)
    try:
        return default if raw is None else int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float, env: Mapping[str, str] | None = None) -> float:
    raw = _raw(name, env)
    try:
        return default if raw is None else float(raw)
    except ValueError:
        return default


def env_list(
    name: str, default: tuple[str, ...], env: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    raw = _raw(name, env)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def env_str(name: str, default: str | None, env: Mapping[str, str] | None = None) -> str | None:
    raw = _raw(name, env)
    return default if raw is None else raw
