"""Load a ``plugins/<tier>/<name>.plugin.py`` file from its path, as the registry does."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PLUGINS = Path(__file__).resolve().parents[2] / "plugins"


def load_module(tier: str, filename: str):
    path = _PLUGINS / tier / filename
    spec = importlib.util.spec_from_file_location(f"{path.stem}_{tier}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
