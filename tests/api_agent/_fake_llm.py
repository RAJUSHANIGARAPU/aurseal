"""A stand-in for the LLM client used by the generation and healing agents.

It subclasses ``BaseLLMClient`` rather than duck-typing it, so the agents get
the real ``complete_result()`` default and the tests exercise the same
"did the call happen" path production does. Nothing leaves the machine, and
every prompt is kept so a test can prove the agent actually asked.
"""

from __future__ import annotations

from api.llm.base import BaseLLMClient


class FakeLLM(BaseLLMClient):
    """Returns canned text for every completion. Records the prompts it saw."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.prompts: list[str] = []

    def complete(self, prompt: str, max_tokens: int = 2048) -> str:  # noqa: ARG002
        self.prompts.append(prompt)
        return self.text
