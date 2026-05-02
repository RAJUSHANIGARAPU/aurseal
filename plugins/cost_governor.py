"""Cost governor — budget tracking, model selection, and prompt caching."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable

_FALLBACK_MODEL = "claude-haiku-4-5-20251001"


class CostGovernor:
    """Tracks LLM spend and downgrades models when budget is running low."""

    def __init__(self, budget_total: float = 5.0) -> None:
        env_val = os.environ.get("PLUGIN_BUDGET_USD")
        self.budget_total: float = float(env_val) if env_val else budget_total
        self.budget_used: float = 0.0
        self._cache: dict[str, str] = {}

    @property
    def budget_remaining(self) -> float:
        return max(0.0, self.budget_total - self.budget_used)

    def get_model(self, preferred: str) -> str:
        """Return preferred model if >= 20% budget remains, else haiku fallback."""
        if self.budget_total <= 0:
            return _FALLBACK_MODEL
        ratio = self.budget_remaining / self.budget_total
        return preferred if ratio >= 0.20 else _FALLBACK_MODEL

    def record(self, model: str, tokens: int, cost_usd: float) -> None:
        """Accumulate spend against the budget."""
        self.budget_used += cost_usd

    def cached_complete(self, prompt: str, call_fn: Callable[[str], str]) -> str:
        """Return cached LLM response for identical prompt content."""
        key = hashlib.sha256(prompt.encode()).hexdigest()
        if key in self._cache:
            return self._cache[key]
        result = call_fn(prompt)
        self._cache[key] = result
        return result
