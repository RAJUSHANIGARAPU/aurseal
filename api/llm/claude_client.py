"""
Claude LLM client — Anthropic Messages API, with a narrow CLI fallback.

The fallback used to be a catch-all: one ``except Exception: pass`` around the
whole HTTP call, falling through to ``claude -p`` as a 120-second subprocess
running the *same* prompt. So a 401, a 429, a DNS blip and a response whose JSON
we could not parse were one indistinguishable outcome, each costing a second
full execution, with no log line saying which had happened. Under
``master_orchestrator``'s ``ThreadPoolExecutor(max_workers=8)`` a rate-limit
episode became up to eight concurrent two-minute subprocesses — the throttle
multiplied instead of absorbed.

The fallback is now deliberate, and it fires for exactly two classes:

* **no API key** — the original, intended purpose: an authenticated CLI is the
  credential when the environment has none.
* **connection error** — DNS/TCP/TLS never reached the endpoint, so nothing was
  spent and the CLI may route differently (proxy, different host config).

Everything else fails fast and says so:

* **401/403** — a wrong or revoked key is not fixed by asking again.
* **429** — the CLI bills the same upstream account, so retrying the prompt is
  the throttle amplifier described above.
* **timeout** — the request may already have been accepted and billed; re-running
  the prompt pays for it twice.
* **5xx** — the CLI talks to the same service; a second full execution has the
  same odds and holds a worker for two minutes.
* **malformed 200** — the call succeeded and was billed. Our parser is wrong, or
  the schema moved. Repeating it changes nothing.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

import requests

from api.llm.base import (
    AUTH,
    CLI_ERROR,
    CLI_MISSING,
    CLI_TIMEOUT,
    CLIENT_ERROR,
    CONNECTION,
    MALFORMED_RESPONSE,
    NO_CREDENTIALS,
    RATE_LIMITED,
    SERVER_ERROR,
    TIMEOUT,
    BaseLLMClient,
    Completion,
)

log = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_API_URL = "https://api.anthropic.com/v1/messages"
_CLI_TIMEOUT_S = 120

# The CLI takes no token cap, so a caller asking for 20 tokens could get an
# essay — which then lands in the cost governor's cache and in reports. Four
# characters per token is the usual rough conversion; capping the bytes keeps
# the caller's budget meaning something.
_CHARS_PER_TOKEN = 4


def _call_claude_cli(prompt: str, max_tokens: int) -> Completion:
    """Call Claude via the `claude -p` CLI."""
    if not shutil.which("claude"):
        return Completion.failed(CLI_MISSING, "`claude` is not on PATH")
    try:
        result = subprocess.run(
            # The prompt goes on stdin, never in argv. Healer prompts carry page
            # DOM and argv is world-readable through `ps` on a shared runner.
            ["claude", "-p"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        log.warning("claude CLI timed out after %ss; giving up", _CLI_TIMEOUT_S)
        return Completion.failed(CLI_TIMEOUT, f"no output within {_CLI_TIMEOUT_S}s")
    except OSError as exc:
        log.warning("claude CLI could not be run: %s", exc)
        return Completion.failed(CLI_ERROR, str(exc))

    if result.returncode != 0:
        detail = (result.stderr or "").strip()[:500]
        log.warning("claude CLI exited %s: %s", result.returncode, detail)
        return Completion.failed(CLI_ERROR, detail or f"exit code {result.returncode}")

    text = (result.stdout or "").strip()
    limit = max(1, max_tokens) * _CHARS_PER_TOKEN
    if len(text) > limit:
        log.warning(
            "claude CLI returned %s chars for a %s-token request; truncating to %s",
            len(text),
            max_tokens,
            limit,
        )
        text = text[:limit]
    if not text:
        return Completion.failed(CLI_ERROR, "CLI exited 0 with no output")
    return Completion(text=text)


def _classify_http_error(exc: requests.HTTPError) -> Completion:
    """Turn an HTTP status into a named failure, so a log line can name it too."""
    status = exc.response.status_code if exc.response is not None else 0
    if status in (401, 403):
        kind = AUTH
    elif status == 429:
        kind = RATE_LIMITED
    elif 400 <= status < 500:
        kind = CLIENT_ERROR
    else:
        kind = SERVER_ERROR
    return Completion.failed(kind, f"HTTP {status}")


class ClaudeLLMClient(BaseLLMClient):
    """Calls the Anthropic Messages API, falling back to `claude -p` CLI.

    Priority:
      1. Anthropic REST API (requires ANTHROPIC_API_KEY)
      2. `claude -p` CLI subprocess — only when there is no key, or when the API
         host could not be reached at all. See the module docstring for why the
         other failure classes deliberately do not fall back.
    """

    def __init__(self, api_key: str | None = None, model: str = _MODEL) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model

    def complete(self, prompt: str, max_tokens: int = 2048) -> str:
        """Send prompt to Claude and return the response text, empty on failure."""
        return self.complete_result(prompt, max_tokens=max_tokens).text

    def complete_result(self, prompt: str, max_tokens: int = 2048) -> Completion:
        """Send prompt to Claude and return the text or a named failure."""
        if not self.api_key:
            log.debug("no ANTHROPIC_API_KEY; using the claude CLI")
            return self._cli_or_no_credentials(prompt, max_tokens)

        result = self._call_api(prompt, max_tokens)
        if result.ok:
            return result

        if result.failure is not None and result.failure.kind == CONNECTION:
            log.warning("anthropic API unreachable (%s); trying the CLI", result.failure.detail)
            return self._cli_or_no_credentials(prompt, max_tokens)

        log.error("anthropic API call failed (%s) — not falling back", result.failure)
        return result

    def _cli_or_no_credentials(self, prompt: str, max_tokens: int) -> Completion:
        result = _call_claude_cli(prompt, max_tokens)
        if not result.ok and result.failure is not None and result.failure.kind == CLI_MISSING:
            return Completion.failed(NO_CREDENTIALS, "no API key and no claude CLI on PATH")
        return result

    def _call_api(self, prompt: str, max_tokens: int) -> Completion:
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
        except requests.HTTPError as exc:
            return _classify_http_error(exc)
        except requests.Timeout as exc:
            return Completion.failed(TIMEOUT, str(exc))
        except requests.ConnectionError as exc:
            return Completion.failed(CONNECTION, str(exc))
        except requests.RequestException as exc:
            return Completion.failed(CLIENT_ERROR, str(exc))

        try:
            text = resp.json()["content"][0]["text"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return Completion.failed(MALFORMED_RESPONSE, f"{type(exc).__name__}: {exc}")

        if not isinstance(text, str):
            return Completion.failed(MALFORMED_RESPONSE, f"content text was {type(text).__name__}")
        return Completion(text=text)
