"""
Loading a tier-4 plugin, and answering for the model without a network.

Plugin files are named ``*.plugin.py``, which is not an importable module name —
the registry loads them through ``importlib.util.spec_from_file_location`` and
so does this.

``StubGovernor`` replaces ``CostGovernor.cached_complete`` and never calls the
``call_fn`` it is handed, so ``ClaudeLLMClient.complete`` is unreachable from
these tests by construction rather than by discipline. Nothing here leaves the
machine.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from plugins.cost_governor import CostGovernor

_TIER4 = Path(__file__).resolve().parents[2] / "plugins" / "tier4"


def load(stem: str):
    """Import ``plugins/tier4/{stem}.plugin.py`` as a throwaway module."""
    path = _TIER4 / f"{stem}.plugin.py"
    spec = importlib.util.spec_from_file_location(f"{stem}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubGovernor(CostGovernor):
    """A governor whose model call is canned. Counts how often it was asked."""

    def __init__(self, *replies: str) -> None:
        super().__init__()
        self._replies = list(replies) or [""]
        self.calls = 0

    def cached_complete(self, prompt, call_fn):  # noqa: ARG002
        reply = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return reply


def break_the_model(monkeypatch) -> None:
    """Make constructing the client raise, the way a missing dependency does."""
    import api.llm.claude_client as claude_client

    class _Unconstructable:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            raise RuntimeError("no credentials configured")

    monkeypatch.setattr(claude_client, "ClaudeLLMClient", _Unconstructable)


def venv_tree(root: Path) -> Path:
    """A dependency tree of the shape that used to be scanned as source.

    Returns the directory holding the one real source file, so a caller can
    assert the walk found that and nothing under ``.venv``.
    """
    dependency = root / ".venv" / "lib" / "python3.12" / "site-packages" / "vendored"
    dependency.mkdir(parents=True)
    (dependency / "dep.py").write_text(
        "import anthropic\n\n\nclass Vendored:\n"
        "    state = 'x'\n\n"
        "    def transition_to(self, s):\n        return s\n\n\n"
        "def vendored_helper():\n    return 1\n",
        encoding="utf-8",
    )
    return root
