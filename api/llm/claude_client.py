"""Claude LLM client — tries Anthropic API first, falls back to `claude -p` CLI."""

import os
import shutil
import subprocess

import requests

from api.llm.base import BaseLLMClient

_MODEL = "claude-sonnet-4-6"
_API_URL = "https://api.anthropic.com/v1/messages"


def _call_claude_cli(prompt: str) -> str | None:
    """Call Claude via the `claude -p` CLI. Returns None if CLI not found."""
    if not shutil.which("claude"):
        return None
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return f"claude CLI error: {result.stderr[:500]}"
    except subprocess.TimeoutExpired:
        return "claude CLI timeout (120s)"
    except Exception as exc:  # noqa: BLE001
        return f"claude CLI error: {exc}"


class ClaudeLLMClient(BaseLLMClient):
    """Calls the Anthropic Messages API, falling back to `claude -p` CLI.

    Priority:
      1. Anthropic REST API (requires ANTHROPIC_API_KEY)
      2. `claude -p` CLI subprocess (requires claude CLI to be authenticated)
    """

    def __init__(self, api_key: str | None = None, model: str = _MODEL) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model

    def complete(self, prompt: str, max_tokens: int = 2048) -> str:
        """Send prompt to Claude and return the response text."""
        if self.api_key:
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
            except Exception:  # noqa: BLE001
                pass  # fall through to CLI

        cli_result = _call_claude_cli(prompt)
        if cli_result is not None:
            return cli_result
        return ""
