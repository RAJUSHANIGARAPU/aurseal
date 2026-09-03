"""
Shared fixtures for the tier-1 plugin tests.

Nothing here reaches Anthropic or the network. ``FakeLLM`` replaces
``ClaudeLLMClient`` at the attribute the plugin imports it from, so the plugin's
own call path — governor cache included — runs exactly as it does in
production; ``FakeProcess`` replaces ``subprocess.run`` so a pytest exit code can
be chosen without launching one.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

from api.llm.base import Completion

_TIER1 = Path(__file__).resolve().parents[2] / "plugins" / "tier1"


def load(stem: str):
    """Load a ``*.plugin.py`` by stem and return the module."""
    path = _TIER1 / f"{stem}.plugin.py"
    spec = importlib.util.spec_from_file_location(f"{stem}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeLLM:
    """Stands in for ``ClaudeLLMClient``. Returns canned completions, in order.

    The last completion repeats, so a test that only cares about one outcome
    does not have to count files.
    """

    def __init__(self, *completions: Completion) -> None:
        self.completions = list(completions) or [Completion(text="assert True\n")]
        self.prompts: list[str] = []

    def complete_result(self, prompt: str, max_tokens: int = 2048) -> Completion:  # noqa: ARG002
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self.completions) - 1)
        return self.completions[index]

    def complete(self, prompt: str, max_tokens: int = 2048) -> str:
        return self.complete_result(prompt, max_tokens=max_tokens).text

    def install(self, monkeypatch) -> FakeLLM:
        """Patch the class the plugin imports, so no version of it can dial out."""
        monkeypatch.setattr(
            "api.llm.claude_client.ClaudeLLMClient",
            lambda *a, **kw: self,  # noqa: ARG005
        )
        return self


class FakeProcess:
    """A finished ``subprocess.run`` result with a chosen exit code."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakePytest:
    """Replaces ``subprocess.run``. Records the argv it was handed."""

    def __init__(self, returncode: int = 0, raises: BaseException | None = None) -> None:
        self.returncode = returncode
        self.raises = raises
        self.calls: list[dict] = []

    def install(self, monkeypatch) -> FakePytest:
        monkeypatch.setattr(subprocess, "run", self._run)
        return self

    @property
    def target(self) -> str:
        """The path pytest was pointed at. Fails loudly if it was never run."""
        assert len(self.calls) == 1, f"expected exactly one pytest run, got {len(self.calls)}"
        return self.calls[0]["argv"][3]

    def _run(self, argv, **kwargs):  # noqa: ANN001
        self.calls.append({"argv": list(argv), "kwargs": kwargs})
        if self.raises is not None:
            raise self.raises
        return FakeProcess(self.returncode, stdout="fake pytest output\n")


def write_module(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path
