"""Classifies UI test failures from failure bundles and extracts healing hints."""

from __future__ import annotations

import re

from api.llm.base import BaseLLMClient
from api.llm.claude_client import ClaudeLLMClient
from autonomous_ui.models import FailureAnalysis, FailureBundle, FailureType

# Selector strings in Playwright errors appear inside locator('...') calls
_SELECTOR_RE = re.compile(r"locator\(['\"](.+?)['\"]\)")

_TIMEOUT_SIGNALS = ("TimeoutError", "Timeout", "timeout exceeded", "Timeout 30000ms")
_LOCATOR_SIGNALS = (
    "strict mode violation",
    "waiting for locator",
    "No element found",
    "locator.click:",
    "locator.fill:",
    "locator.wait_for:",
    "locator resolved to",
)
_NAV_SIGNALS = (
    "net::ERR",
    "NavigationError",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_TIMED_OUT",
    "net::ERR_ABORTED",
)


class FailureAnalyzer:
    """Classifies a failure bundle and optionally calls the LLM for a deeper diagnosis."""

    def __init__(self, llm: BaseLLMClient | None = None) -> None:
        self._llm = llm or ClaudeLLMClient()

    def analyze(self, bundle: FailureBundle, use_llm: bool = True) -> FailureAnalysis:
        full_text = bundle.error + "\n" + bundle.stack_trace
        failure_type = self._classify(full_text)
        selectors = list(dict.fromkeys(_SELECTOR_RE.findall(full_text)))  # ordered, deduped
        root_cause, confidence = self._rule_based_cause(failure_type, bundle)

        llm_suggestion = ""
        if use_llm:
            llm_suggestion = self._llm_diagnosis(bundle, failure_type)

        return FailureAnalysis(
            test_name=bundle.test,
            failure_type=failure_type,
            root_cause=root_cause,
            confidence=confidence,
            selectors_mentioned=selectors,
            llm_suggestion=llm_suggestion,
        )

    def _classify(self, text: str) -> FailureType:
        # Order matters: TIMEOUT before LOCATOR because a timed-out locator wait
        # surfaces a TimeoutError, which is a timing issue, not a broken selector.
        if any(s in text for s in _TIMEOUT_SIGNALS):
            return FailureType.TIMEOUT
        if any(s in text for s in _LOCATOR_SIGNALS):
            return FailureType.LOCATOR
        if any(s in text for s in _NAV_SIGNALS):
            return FailureType.NAVIGATION
        if "AssertionError" in text:
            return FailureType.ASSERTION
        return FailureType.UNKNOWN

    def _rule_based_cause(self, ft: FailureType, bundle: FailureBundle) -> tuple[str, str]:
        head = bundle.error[:300]
        if ft == FailureType.TIMEOUT:
            return f"Element did not become visible within timeout: {head}", "high"
        if ft == FailureType.LOCATOR:
            return f"No DOM element matched the selector: {head}", "high"
        if ft == FailureType.NAVIGATION:
            return f"Page failed to load or network error: {head}", "high"
        if ft == FailureType.ASSERTION:
            return f"Assertion mismatch — actual value differed from expected: {head}", "medium"
        return f"Unrecognised failure: {head}", "low"

    def _llm_diagnosis(self, bundle: FailureBundle, ft: FailureType) -> str:
        dom_section = (
            f"\n\nDOM SNAPSHOT (first 3000 chars):\n{bundle.dom_snapshot[:3000]}"
            if bundle.dom_snapshot
            else ""
        )
        prompt = f"""You are a Playwright automation expert diagnosing a UI test failure.

TEST: {bundle.test}
FAILURE TYPE: {ft.value}
ERROR: {bundle.error}

STACK TRACE:
{bundle.stack_trace[:1500]}

CONSOLE ERRORS: {bundle.console_errors}
FAILED NETWORK REQUESTS: {bundle.failed_requests}{dom_section}

Reply in exactly this format — no extra text:
ROOT CAUSE: <one sentence>
HEALING SUGGESTION: <concrete action — exact selector to try, wait to add, or assertion fix>
CONFIDENCE: high|medium|low"""
        return self._llm.complete(prompt, max_tokens=512)
