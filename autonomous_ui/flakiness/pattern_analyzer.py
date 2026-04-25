"""Classifies the root cause of flakiness from error patterns and run metadata.

Classification order:
  1. Rule-based (fast, free) — high-signal keywords in error text
  2. Parallel vs sequential comparison — detects resource contention
  3. LLM classification (fallback) — for ambiguous patterns

When NOT to classify:
  - Very low confidence profiles (not enough history)
  - The `is_flaky` flag is False
"""

from __future__ import annotations

from api.llm.base import BaseLLMClient
from api.llm.claude_client import ClaudeLLMClient
from autonomous_ui.flakiness.models import FlakinessProfile, FlakPattern, FlakRecord

_TIMING_SIGNALS = (
    "TimeoutError",
    "Timeout",
    "timeout exceeded",
    "waiting for locator",
    "ElementNotVisible",
    "not visible",
    "wait_for",
    "waitForSelector",
    "animation",
)
_ENVIRONMENT_SIGNALS = (
    "net::ERR",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_TIMED_OUT",
    "ConnectionRefused",
    "ECONNREFUSED",
    "socket",
    "SSLError",
)
_DATA_SIGNALS = (
    "AssertionError",
    "IntegrityError",
    "UniqueViolation",
    "already exists",
    "duplicate key",
    "KeyError",
    "IndexError",
)
_LOCATOR_SIGNALS = (
    "strict mode violation",
    "No element found",
    "locator.click:",
    "locator.fill:",
)


class PatternAnalyzer:
    """Classifies the root cause of flakiness for a test profile."""

    def __init__(self, llm: BaseLLMClient | None = None) -> None:
        self._llm = llm or ClaudeLLMClient()

    def classify(
        self,
        profile: FlakinessProfile,
        records: list[FlakRecord],
        use_llm: bool = True,
    ) -> FlakPattern:
        """Return the most likely FlakPattern for this profile."""
        error_corpus = " ".join(r.error for r in records if r.error)

        # Rule 1: environment signals are high-confidence
        if any(s in error_corpus for s in _ENVIRONMENT_SIGNALS):
            return FlakPattern.ENVIRONMENT

        # Rule 2: timing signals are the most common UI flakiness cause
        if any(s in error_corpus for s in _TIMING_SIGNALS):
            return FlakPattern.TIMING

        # Rule 3: locator errors mixed with passes indicate intermittent DOM change
        if any(s in error_corpus for s in _LOCATOR_SIGNALS):
            return FlakPattern.TIMING  # DOM not ready = timing variant

        # Rule 4: data errors — assertion values change between runs
        if any(s in error_corpus for s in _DATA_SIGNALS):
            return FlakPattern.DATA_POLLUTION

        # Rule 5: resource contention — failures cluster in parallel workers
        if self._is_parallel_contention(records):
            return FlakPattern.RESOURCE_CONTENTION

        # Rule 6: order-dependency heuristic — no error text but non-deterministic
        if not error_corpus.strip() and profile.flakiness_rate < 0.5:
            return FlakPattern.ORDER_DEPENDENT

        # Fallback: LLM
        if use_llm:
            return self._llm_classify(profile, records)

        return FlakPattern.UNKNOWN

    def _is_parallel_contention(self, records: list[FlakRecord]) -> bool:
        """Return True when failures are disproportionately concentrated in xdist workers."""
        parallel_fails = sum(1 for r in records if r.worker != "main" and r.outcome == "failed")
        sequential_fails = sum(1 for r in records if r.worker == "main" and r.outcome == "failed")
        parallel_runs = sum(1 for r in records if r.worker != "main")
        sequential_runs = sum(1 for r in records if r.worker == "main")

        if parallel_runs < 3 or sequential_runs < 3:
            return False

        parallel_rate = parallel_fails / parallel_runs
        sequential_rate = sequential_fails / sequential_runs
        # Contention is likely when parallel failure rate is at least 3x sequential
        return parallel_rate >= 3 * sequential_rate and parallel_rate > 0.1

    def _llm_classify(self, profile: FlakinessProfile, records: list[FlakRecord]) -> FlakPattern:
        sample_errors = list({r.error for r in records if r.error})[:5]
        prompt = f"""You are a QA expert classifying the root cause of flaky test behaviour.

TEST: {profile.test_id}
FLAKINESS RATE: {profile.flakiness_rate:.1%} over {profile.total_runs} runs
MAX CONSECUTIVE FAILURES: {profile.max_consecutive_failures}
SAMPLE ERRORS (up to 5 unique):
{chr(10).join(f"  - {e[:300]}" for e in sample_errors)}

Classify as exactly ONE of:
  timing           — element not ready, animation, implicit wait missing
  order_dependent  — test state depends on execution order
  resource_contention — parallel test workers competing for shared resource
  data_pollution   — shared test data mutated by another test
  environment      — network, DNS, or external service instability
  unknown          — cannot determine

Reply with ONLY the single classification word, nothing else."""

        raw = self._llm.complete(prompt, max_tokens=20).strip().lower()
        try:
            return FlakPattern(raw)
        except ValueError:
            return FlakPattern.UNKNOWN
