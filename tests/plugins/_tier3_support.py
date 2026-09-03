"""Shared scaffolding for the tier-3 plugin tests.

Tier-3 plugin files are ``*.plugin.py``, which is not an importable module name,
so they are loaded from their path the same way ``PluginRegistry`` loads them —
the module-level code under test runs exactly as it does in a real scan.

Nothing here reaches Anthropic. ``StubGovernor`` replaces ``cached_complete``
outright, which is the single seam every tier-3 plugin's model call goes
through, so the plugins can be exercised across outage, garbage and healthy
replies without a network stack being involved at all.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from plugins.cost_governor import CostGovernor

_TIER3 = Path(__file__).resolve().parents[2] / "plugins" / "tier3"


def load_module(filename: str):
    """Import ``plugins/tier3/<filename>`` from its path, as the registry does."""
    path = _TIER3 / filename
    spec = importlib.util.spec_from_file_location(f"{path.stem}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_plugin(filename: str, class_name: str):
    """Instantiate the plugin class in ``plugins/tier3/<filename>``."""
    return getattr(load_module(filename), class_name)()


class StubGovernor(CostGovernor):
    """A governor whose model call is canned, and which counts being asked."""

    def __init__(self, reply: str = "") -> None:
        super().__init__()
        self.reply = reply
        self.calls = 0
        self.prompts: list[str] = []

    def cached_complete(self, prompt, call_fn):  # noqa: ARG002
        self.calls += 1
        self.prompts.append(prompt)
        return self.reply


def write_source_tree(root: Path, files: dict[str, str]) -> Path:
    """Write ``{relative path: content}`` under ``root`` and return it."""
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root
