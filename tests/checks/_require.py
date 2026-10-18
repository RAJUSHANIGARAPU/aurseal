"""Skip a test cleanly when an optional check tool is absent, or fail when it must be there.

The offline suite runs without ``requirements-checks.txt`` and without the k6 / gitleaks
binaries, so tests that drive a real tool skip there. A job that installed the tools sets
``CHECKS_REQUIRE_TOOLS=true``: a missing tool then fails the test instead of skipping it,
so a broken install cannot turn the checks job into a row of skips.
"""

from __future__ import annotations

import importlib
import os
import shutil
from types import ModuleType

import pytest

REQUIRE_VARIABLE = "CHECKS_REQUIRE_TOOLS"


def _required() -> bool:
    return os.environ.get(REQUIRE_VARIABLE, "").strip().lower() in ("1", "true", "yes", "on")


def _absent(message: str) -> None:
    if _required():
        pytest.fail(f"{message} ({REQUIRE_VARIABLE} is set, so a missing tool is a failure)")
    pytest.skip(message)


def require_module(name: str) -> ModuleType:
    try:
        return importlib.import_module(name)
    except ImportError:
        _absent(f"{name} is not installed (pip install -r requirements-checks.txt)")
        raise  # unreachable: _absent always raises


def require_binary(name: str, env_var: str | None = None) -> str:
    explicit = os.environ.get(env_var) if env_var else None
    found = explicit if explicit and os.path.isfile(explicit) else shutil.which(name)
    if not found:
        _absent(f"{name} is not on PATH" + (f" and {env_var} is not set" if env_var else ""))
    return found  # type: ignore[return-value]
