"""Generates targeted fix recommendations for flaky tests.

Each pattern maps to a concrete strategy. LLM is used to produce specific,
file-aware suggestions when a strategy is identified.

Safety rules:
  - Never auto-apply code changes without explicit opt-in
  - Suggestions explain WHY, not just what to change
  - Resource contention fixes propose architectural changes, not code patches
"""

from __future__ import annotations

import json

from api.llm.base import BaseLLMClient
from api.llm.claude_client import ClaudeLLMClient
from autonomous_ui.flakiness.models import (
    FlakinessProfile,
    FlakPattern,
    FlakRecord,
    RemediationResult,
)

_STRATEGY_MAP = {
    FlakPattern.TIMING: "add_explicit_wait",
    FlakPattern.ORDER_DEPENDENT: "isolate_test",
    FlakPattern.RESOURCE_CONTENTION: "fix_parallelism",
    FlakPattern.DATA_POLLUTION: "isolate_test_data",
    FlakPattern.ENVIRONMENT: "add_resilience",
    FlakPattern.UNKNOWN: "suggest_only",
}


class FlakinessRemediator:
    """Produces RemediationResult objects with actionable fix guidance."""

    def __init__(self, llm: BaseLLMClient | None = None) -> None:
        self._llm = llm or ClaudeLLMClient()

    def remediate(
        self,
        profile: FlakinessProfile,
        pattern: FlakPattern,
        records: list[FlakRecord],
        use_llm: bool = True,
    ) -> RemediationResult:
        """
        Produce a fix recommendation for one flaky test.

        ``use_llm`` mirrors :meth:`PatternAnalyzer.classify`. It exists because
        this method had no such switch while ``classify`` did, so a caller that
        had already said "no LLM" for classification had no way to say it here
        and got a blocking model call anyway — see the note on the pytest
        plugin, where that cost roughly 23 seconds per flaky test at the end of
        every single run.
        """
        strategy = _STRATEGY_MAP.get(pattern, "suggest_only")
        suggestion = self._build_suggestion(profile, pattern, records, strategy, use_llm)
        return RemediationResult(
            test_id=profile.test_id,
            pattern=pattern,
            strategy=strategy,
            suggestion=suggestion,
            auto_applied=False,
        )

    def _build_suggestion(
        self,
        profile: FlakinessProfile,
        pattern: FlakPattern,
        records: list[FlakRecord],
        strategy: str,
        use_llm: bool = True,
    ) -> str:
        base = self._rule_suggestion(profile, pattern, records)
        if use_llm and self._llm and base:
            # Enrich the rule-based suggestion with LLM specifics
            llm_detail = self._llm_enrich(profile, pattern, records, base)
            return f"{base}\n\nLLM DETAIL:\n{llm_detail}" if llm_detail else base
        return base or "No automated suggestion — manual investigation required."

    def _rule_suggestion(
        self, profile: FlakinessProfile, pattern: FlakPattern, records: list[FlakRecord]
    ) -> str:
        if pattern == FlakPattern.TIMING:
            return self._timing_suggestion(profile, records)
        if pattern == FlakPattern.ORDER_DEPENDENT:
            return self._order_dep_suggestion(profile)
        if pattern == FlakPattern.RESOURCE_CONTENTION:
            return self._resource_contention_suggestion(profile, records)
        if pattern == FlakPattern.DATA_POLLUTION:
            return self._data_pollution_suggestion(profile)
        if pattern == FlakPattern.ENVIRONMENT:
            return self._environment_suggestion(profile, records)
        return ""

    def _timing_suggestion(self, profile: FlakinessProfile, records: list[FlakRecord]) -> str:
        avg_fail_dur = sum(r.duration_s for r in records if r.outcome == "failed") / max(
            sum(1 for r in records if r.outcome == "failed"), 1
        )
        avg_pass_dur = sum(r.duration_s for r in records if r.outcome == "passed") / max(
            sum(1 for r in records if r.outcome == "passed"), 1
        )
        timing_gap = f"failures avg {avg_fail_dur:.1f}s vs passes avg {avg_pass_dur:.1f}s"
        rate_pct = f"{profile.flakiness_rate:.1%}"
        return (
            f"TIMING — {profile.test_id} fails {rate_pct} of the time ({timing_gap}).\n"
            f"Likely cause: implicit wait or animation not accounted for.\n"
            f"Fix: replace hard waits (time.sleep) with page.wait_for_selector() or "
            f"expect(locator).to_be_visible(). If the element appears after a network call, "
            f"add page.wait_for_load_state('networkidle') before the interaction."
        )

    def _order_dep_suggestion(self, profile: FlakinessProfile) -> str:
        return (
            f"ORDER-DEPENDENT — {profile.test_id} outcome depends on which tests ran before it.\n"
            f"Fix: ensure the test sets up its own preconditions in a @pytest.fixture "
            f"with 'function' scope and tears them down unconditionally in a yield fixture. "
            f"Run with pytest --randomly to surface ordering issues systematically."
        )

    def _resource_contention_suggestion(
        self, profile: FlakinessProfile, records: list[FlakRecord]
    ) -> str:
        worker_fail_rates = {}
        for worker in {r.worker for r in records if r.worker != "main"}:
            w_records = [r for r in records if r.worker == worker]
            w_fails = sum(1 for r in w_records if r.outcome == "failed")
            worker_fail_rates[worker] = w_fails / len(w_records) if w_records else 0.0
        worst_worker = max(worker_fail_rates, key=worker_fail_rates.get, default="unknown")
        worst_rate = f"{worker_fail_rates.get(worst_worker, 0):.1%}"
        return (
            f"RESOURCE CONTENTION — {profile.test_id} fails more under parallel execution "
            f"(worst: {worst_worker} at {worst_rate} failure rate).\n"
            f"Fix: isolate the shared resource (browser profile, port, DB sequence, file). "
            f"Use pytest-xdist --dist loadfile to keep related tests on the same worker, "
            f"or use a pytest.fixture with scope='session' + a threading.Lock() to serialise "
            f"access to the shared resource."
        )

    def _data_pollution_suggestion(self, profile: FlakinessProfile) -> str:
        return (
            f"DATA POLLUTION — {profile.test_id} fails when shared test data was modified by a "
            f"prior test run.\n"
            f"Fix: give each test its own data via a function-scoped fixture that creates and "
            f"deletes data independently. Never rely on a fixed record ID or state left by another "
            f"test. If using a database, wrap tests in a transaction and rollback in teardown."
        )

    def _environment_suggestion(self, profile: FlakinessProfile, records: list[FlakRecord]) -> str:
        env_breakdown = {}
        for env in {r.environment for r in records}:
            env_records = [r for r in records if r.environment == env]
            env_fails = sum(1 for r in env_records if r.outcome == "failed")
            env_breakdown[env] = env_fails / len(env_records) if env_records else 0.0
        worst_env = max(env_breakdown, key=env_breakdown.get, default="unknown")
        return (
            f"ENVIRONMENT — {profile.test_id} fails due to external service instability "
            f"(worst in: {worst_env} at {env_breakdown.get(worst_env, 0):.1%} failure rate).\n"
            f"Fix: add retry logic with tenacity or pytest-rerunfailures scoped ONLY to this test "
            f"(@pytest.mark.flaky(reruns=2)). For external API calls, add a wait_for_condition "
            f"with exponential backoff. Long-term: use WireMock or responses library to mock "
            f"the external dependency in tests."
        )

    def _llm_enrich(
        self,
        profile: FlakinessProfile,
        pattern: FlakPattern,
        records: list[FlakRecord],
        base_suggestion: str,
    ) -> str:
        sample_errors = list({r.error for r in records if r.error})[:3]
        prompt = (
            "You are a QA engineer providing specific, file-level fix guidance for a flaky test.\n"
            f"\nTEST: {profile.test_id}"
            f"\nPATTERN: {pattern.value}"
            f"\nFAILURE RATE: {profile.flakiness_rate:.1%}"
            f"\nSAMPLE ERRORS:\n{json.dumps(sample_errors, indent=2)}"
            f"\n\nBASE SUGGESTION:\n{base_suggestion}"
            "\n\nProvide ONE concrete, specific code change (file path + what to change)."
            "\nIf the test file path is identifiable from the test ID, reference it directly."
            "\nKeep under 150 words. No preamble."
        )
        return self._llm.complete(prompt, max_tokens=256)
