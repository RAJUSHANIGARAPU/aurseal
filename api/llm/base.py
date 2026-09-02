"""
The LLM client interface, and the vocabulary for saying a call did not happen.

``complete()`` has always been documented as returning an empty string on
failure. The Claude client did not honour that — it returned prose like
``"claude CLI timeout (120s)"`` — and callers, having no way to tell prose apart
from a model answer, treated an outage as content. The worst of them turned it
into a definitive verdict (``behavioral_equivalence``: anything unrecognised was
mapped to ``"semantic"``, i.e. "the refactor changed behaviour"), and the cost
governor then cached that verdict by prompt hash for the rest of the process.

So there are two things here, not one:

``complete()`` keeps its contract exactly — a string, empty on failure — because
nine callers rely on it and most of them only need "did I get text or not".

``complete_result()`` is for callers that must not guess. It returns a
``Completion`` carrying either text or an ``LLMFailure`` naming the class of
failure, so "the model said nothing" and "the model was never reached" stop
being the same value.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

# Failure kinds. Stable strings, safe to branch on and safe to log.
NO_CREDENTIALS = "no_credentials"  # no API key and no usable CLI
AUTH = "auth"  # 401/403 — the key is wrong, revoked, or lacks the model
RATE_LIMITED = "rate_limited"  # 429 — quota or concurrency limit
CLIENT_ERROR = "client_error"  # other 4xx — malformed request, our bug
SERVER_ERROR = "server_error"  # 5xx — upstream trouble
TIMEOUT = "timeout"  # the request may have been accepted and billed
CONNECTION = "connection"  # DNS/TCP/TLS — the endpoint was never reached
MALFORMED_RESPONSE = "malformed_response"  # 200 whose body is not the shape we parse
CLI_MISSING = "cli_missing"  # `claude` is not on PATH
CLI_ERROR = "cli_error"  # the subprocess exited non-zero
CLI_TIMEOUT = "cli_timeout"  # the subprocess outlived its deadline


@dataclass(frozen=True)
class LLMFailure:
    """Why no completion came back. ``kind`` is one of the constants above."""

    kind: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}" if self.detail else self.kind


@dataclass(frozen=True)
class Completion:
    """Either model text or a named failure — never a failure disguised as text."""

    text: str = ""
    failure: LLMFailure | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None

    @classmethod
    def failed(cls, kind: str, detail: str = "") -> Completion:
        return cls(text="", failure=LLMFailure(kind, detail))


class BaseLLMClient(ABC):
    """Abstract interface for language model clients."""

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 2048) -> str:
        """Send a prompt and return the model response text.

        Returns empty string on failure or when API key is absent. Never returns
        an error message — a caller cannot tell one from an answer.
        """

    def complete_result(self, prompt: str, max_tokens: int = 2048) -> Completion:
        """Same call, but says *why* nothing came back.

        The default derives the answer from ``complete()`` using the contract
        above: empty means failure. Implementations that know more should
        override this and let ``complete()`` delegate to it.
        """
        text = self.complete(prompt, max_tokens=max_tokens)
        if not text:
            return Completion.failed(NO_CREDENTIALS, "complete() returned no text")
        return Completion(text=text)
