"""Cost governor — budget tracking, model selection, and prompt caching.

The governor was decorative. `cached_complete` never called `record()`, and of
the eight call sites only `unit_ai` recorded anything, so `budget_used` stayed at
`0.0` through every tier-3 and tier-4 model call. `get_model` therefore always
returned the preferred model, and since it only ever *downgrades* and never
declines, `PLUGIN_BUDGET_USD=0.01` stopped precisely nothing.

The counter was on the wrong path — the same shape as the crawl budget that was
spent only on success, and as the health score computed only over the plugins
that reported.

Three things changed:

**Accounting moved into the governor.** Every call routed through
`cached_complete` is priced and recorded, so a plugin cannot spend without the
budget noticing. Plugins no longer record separately; doing both would
double-count.

**There is a refusal.** `get_model` downgrading to a cheaper model is not a
budget — it slows the burn and never stops it. Once the budget is exhausted the
governor declines to call at all. It returns an empty string, which the plugins
now read as an outage and report as `unknown`, so an exhausted budget produces
"we did not find out" rather than a cheerful pass.

**Concurrency is handled.** `master_orchestrator` runs plugins on eight threads.
`budget_used += cost` is not atomic, and a plain check-then-act cache let eight
threads miss the same key and all pay for the same prompt. The lock is never
held across a model call — that would serialise the pool — so in-flight prompts
are tracked and a second caller waits for the first rather than duplicating it.

Pricing is an estimate from character counts, not a billing record. It is the
same approximation `unit_ai` already used, and it is honest about being one: the
point is to bound spend, not to reconcile an invoice.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

_FALLBACK_MODEL = "claude-haiku-4-5-20251001"

# Haiku 4.5 list pricing. An estimate: four characters per token is the usual
# rough figure for English, and it is not a substitute for reported usage.
USD_PER_INPUT_TOKEN = 1.00 / 1_000_000
USD_PER_OUTPUT_TOKEN = 5.00 / 1_000_000
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def estimate_cost(prompt: str, response: str) -> tuple[int, float]:
    """Return (tokens, usd) for one exchange, by character count."""
    in_tokens = estimate_tokens(prompt)
    out_tokens = estimate_tokens(response)
    cost = in_tokens * USD_PER_INPUT_TOKEN + out_tokens * USD_PER_OUTPUT_TOKEN
    return in_tokens + out_tokens, cost


class CostGovernor:
    """Tracks LLM spend, declines calls past the budget, and dedupes prompts."""

    def __init__(self, budget_total: float = 5.0) -> None:
        env_val = os.environ.get("PLUGIN_BUDGET_USD")
        self.budget_total: float = float(env_val) if env_val else budget_total
        self.budget_used: float = 0.0
        self.calls_made: int = 0
        self.calls_declined: int = 0
        self.tokens_used: int = 0
        self._cache: dict[str, str] = {}
        self._inflight: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    @property
    def budget_remaining(self) -> float:
        return max(0.0, self.budget_total - self.budget_used)

    @property
    def exhausted(self) -> bool:
        return self.budget_remaining <= 0.0

    def get_model(self, preferred: str) -> str:
        """Return preferred model if >= 20% budget remains, else haiku fallback."""
        if self.budget_total <= 0:
            return _FALLBACK_MODEL
        ratio = self.budget_remaining / self.budget_total
        return preferred if ratio >= 0.20 else _FALLBACK_MODEL

    def record(self, model: str, tokens: int, cost_usd: float) -> None:
        """Accumulate spend against the budget. Thread-safe."""
        with self._lock:
            self.budget_used += cost_usd
            self.tokens_used += tokens
            self.calls_made += 1
        logger.debug(
            "[governor] %s | tokens=%d | $%.6f | used $%.4f of $%.4f",
            model,
            tokens,
            cost_usd,
            self.budget_used,
            self.budget_total,
        )

    def summary(self) -> dict:
        with self._lock:
            return {
                "budget_total": self.budget_total,
                "budget_used": round(self.budget_used, 6),
                "budget_remaining": round(self.budget_remaining, 6),
                "calls_made": self.calls_made,
                "calls_declined": self.calls_declined,
                "tokens_used": self.tokens_used,
                "cached_prompts": len(self._cache),
            }

    def cached_complete(self, prompt: str, call_fn: Callable[[str], str]) -> str:
        """
        Call the model once per distinct prompt, within budget, and account for it.

        Returns the empty string when the budget is exhausted. That is
        deliberately the same value a failed call returns: plugins already treat
        it as "no answer" and report `unknown`, which is the honest outcome for
        a run that could not afford to look.

        Failures are not cached. `complete()` returns an empty string when the
        model was not reached, and memoising that by prompt hash pinned a
        transient 429 to the prompt for the life of the process — every later
        caller got the outage back without a call being made, so the run could
        not recover even after the throttle lifted.
        """
        key = hashlib.sha256(prompt.encode()).hexdigest()

        while True:
            with self._lock:
                if key in self._cache:
                    return self._cache[key]

                waiter = self._inflight.get(key)
                if waiter is None:
                    if self.exhausted:
                        self.calls_declined += 1
                        logger.warning(
                            "[governor] budget exhausted ($%.4f of $%.4f) — declining call; "
                            "the caller will report this as no answer",
                            self.budget_used,
                            self.budget_total,
                        )
                        return ""
                    self._inflight[key] = threading.Event()
                    break

            # Another thread is already asking this exact question. Wait for it
            # rather than paying for the same prompt eight times.
            waiter.wait(timeout=180)
            with self._lock:
                if key in self._cache:
                    return self._cache[key]
                if key not in self._inflight:
                    # It finished without a usable answer. Try once ourselves.
                    continue
            return ""

        try:
            result = call_fn(prompt)
        finally:
            with self._lock:
                event = self._inflight.pop(key, None)
            if event is not None:
                event.set()

        if result:
            tokens, cost = estimate_cost(prompt, result)
            self.record(_FALLBACK_MODEL, tokens, cost)
            with self._lock:
                self._cache[key] = result
        return result
