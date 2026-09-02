"""
Tests for FlakinessRemediator, and specifically for it staying offline.

This module is new because the remediator was the one part of the flakiness
subsystem with no tests at all — detector, history store, pattern analyzer and
reporter each had a file, and the component that reached out to a language
model on every run did not. It was registered unconditionally by the pytest
plugin, so the cost landed on every invocation of pytest in the repository.
"""

from __future__ import annotations

import pytest

from autonomous_ui.flakiness.models import FlakinessProfile, FlakPattern, FlakRecord
from autonomous_ui.flakiness.pytest_plugin import _llm_enabled
from autonomous_ui.flakiness.remediator import FlakinessRemediator


class ExplodingLLM:
    """
    An LLM client that fails the test if it is called.

    Asserting "the suggestion contains no LLM detail" would also pass if the
    call happened and returned nothing, which is exactly what a missing API key
    produces — the assertion would hold while the 23-second subprocess still
    ran. Only refusing to be called proves the call did not happen.
    """

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt: str, max_tokens: int = 2048) -> str:
        self.calls += 1
        raise AssertionError("the LLM was called when it should not have been")


class RecordingLLM:
    def __init__(self, reply: str = "use an explicit wait on the results grid") -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str, max_tokens: int = 2048) -> str:
        self.prompts.append(prompt)
        return self.reply


def _records(n: int = 6) -> list[FlakRecord]:
    return [
        FlakRecord(
            test_id="tests/test_thing.py::test_x",
            run_id=f"run-{i}",
            outcome="failed" if i % 2 else "passed",
            duration_s=1.0 + i,
            error="TimeoutError: waiting for selector" if i % 2 else "",
            timestamp=f"2026-09-0{i + 1}T00:00:00+00:00",
            worker="main",
            environment="qa",
        )
        for i in range(n)
    ]


def _profile() -> FlakinessProfile:
    return FlakinessProfile(
        test_id="tests/test_thing.py::test_x",
        total_runs=6,
        failure_count=3,
        flakiness_rate=0.5,
        confidence=1.0,
        is_flaky=True,
        most_common_error="TimeoutError: waiting for selector",
        avg_duration_s=3.5,
        last_failure_ts="2026-09-06T00:00:00+00:00",
        max_consecutive_failures=1,
    )


class TestUseLlmSwitch:
    def test_use_llm_false_never_calls_the_model(self):
        llm = ExplodingLLM()
        remediator = FlakinessRemediator(llm=llm)

        result = remediator.remediate(_profile(), FlakPattern.TIMING, _records(), use_llm=False)

        assert llm.calls == 0
        assert result.suggestion
        assert "LLM DETAIL" not in result.suggestion

    def test_use_llm_true_does_call_the_model(self):
        """
        Positive control for the switch.

        Without this, a change that disabled enrichment outright would leave the
        test above passing and silently remove the feature rather than gate it.
        """
        llm = RecordingLLM()
        remediator = FlakinessRemediator(llm=llm)

        result = remediator.remediate(_profile(), FlakPattern.TIMING, _records(), use_llm=True)

        assert len(llm.prompts) == 1
        assert "LLM DETAIL" in result.suggestion
        assert llm.reply in result.suggestion

    def test_default_matches_classify(self):
        """
        ``PatternAnalyzer.classify`` defaults to ``use_llm=True``; this mirrors
        it, so callers of the library see one consistent convention. The pytest
        plugin is what opts out, not the library.
        """
        llm = RecordingLLM()
        FlakinessRemediator(llm=llm).remediate(_profile(), FlakPattern.TIMING, _records())
        assert len(llm.prompts) == 1


class TestRuleSuggestionStillWorks:
    @pytest.mark.parametrize(
        "pattern,strategy",
        [
            (FlakPattern.TIMING, "add_explicit_wait"),
            (FlakPattern.ORDER_DEPENDENT, "isolate_test"),
            (FlakPattern.RESOURCE_CONTENTION, "fix_parallelism"),
            (FlakPattern.DATA_POLLUTION, "isolate_test_data"),
            (FlakPattern.ENVIRONMENT, "add_resilience"),
            (FlakPattern.UNKNOWN, "suggest_only"),
        ],
    )
    def test_every_pattern_yields_a_strategy_and_a_suggestion_offline(self, pattern, strategy):
        result = FlakinessRemediator(llm=ExplodingLLM()).remediate(
            _profile(), pattern, _records(), use_llm=False
        )
        assert result.strategy == strategy
        assert result.suggestion.strip()
        assert result.auto_applied is False

    def test_no_records_still_produces_a_suggestion(self):
        result = FlakinessRemediator(llm=ExplodingLLM()).remediate(
            _profile(), FlakPattern.TIMING, [], use_llm=False
        )
        assert result.suggestion.strip()


class TestPluginGate:
    def test_llm_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("ENABLE_FLAKINESS_LLM", raising=False)
        assert _llm_enabled() is False

    @pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "Yes"])
    def test_recognised_opt_in_values(self, monkeypatch, value):
        monkeypatch.setenv("ENABLE_FLAKINESS_LLM", value)
        assert _llm_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "", "maybe"])
    def test_anything_else_stays_off(self, monkeypatch, value):
        monkeypatch.setenv("ENABLE_FLAKINESS_LLM", value)
        assert _llm_enabled() is False
