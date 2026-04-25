"""Claude LLM client using raw requests — matches pattern in core/ai/auto_fixer.py."""

import os

import requests

from api.llm.base import BaseLLMClient

_MODEL = "claude-sonnet-4-6"
_API_URL = "https://api.anthropic.com/v1/messages"


class ClaudeLLMClient(BaseLLMClient):
    """Calls the Anthropic Messages API via raw requests.

    Degrades gracefully when ANTHROPIC_API_KEY is absent.
    """

    def __init__(self, api_key: str | None = None, model: str = _MODEL) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model

    def complete(self, prompt: str, max_tokens: int = 2048) -> str:
        """Send prompt to Claude and return the response text.

        Returns empty string if no API key is configured or on any error.
        """
        if not self.api_key:
            return ""

        try:
            resp = requests.post(
                _API_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
        except Exception as exc:  # noqa: BLE001
            return f"Claude API error: {exc}"
