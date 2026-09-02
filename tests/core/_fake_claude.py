"""
A stand-in for the Anthropic Messages endpoint, shared by the generator tests.

The generator posts with ``requests.post`` inside ``core.ai.test_generator``,
so that name is what gets replaced: the surrounding code — payload assembly,
``raise_for_status``, response parsing — runs exactly as it does in production
and nothing leaves the machine. The fake also keeps every prompt it was sent,
which is how the spec tests prove the spec was actually read.
"""

from __future__ import annotations

import core.ai.test_generator as test_generator

GENERATED = "import pytest\n\n\ndef test_generated():\n    assert True\n"


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"content": [{"text": self._text}]}


class FakeClaude:
    """Replaces ``requests.post``. Returns canned text, or raises."""

    def __init__(self, text: str = GENERATED, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.prompts: list[str] = []

    def install(self, monkeypatch) -> FakeClaude:
        monkeypatch.setattr(test_generator.requests, "post", self._post)
        return self

    @property
    def prompt(self) -> str:
        """The single prompt sent. Fails loudly if the call never happened."""
        assert len(self.prompts) == 1, f"expected exactly one API call, got {len(self.prompts)}"
        return self.prompts[0]

    def _post(self, url, **kwargs):  # noqa: ANN001, ARG002
        self.prompts.append(kwargs["json"]["messages"][0]["content"])
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.text)


def write_trace(tmp_path) -> None:
    """Put one (unparseable, deliberately) trace ZIP where the generator looks."""
    traces = tmp_path / "reports" / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    (traces / "run.zip").write_bytes(b"not really a zip")
