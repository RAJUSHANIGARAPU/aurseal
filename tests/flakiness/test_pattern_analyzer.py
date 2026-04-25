"""Tests for autonomous_ui.flakiness.pattern_analyzer."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from autonomous_ui.flakiness.models import FlakinessProfile, FlakPattern, FlakRecord
from autonomous_ui.flakiness.pattern_analyzer import PatternAnalyzer


def _profile(test_id: str = "test_x", rate: float = 0.15) -> FlakinessProfile:
    return FlakinessProfile(
        test_id=test_id,
        total_runs=20,
        failure_count=int(rate * 20),
        flakiness_rate=rate,
        confidence=1.0,
        is_flaky=True,
        most_common_error="",
        avg_duration_s=2.0,
        last_failure_ts="2026-04-25T00:00:00Z",
        max_consecutive_failures=2,
    )


def _rec(outcome: str = "passed", error: str = "", worker: str = "main") -> FlakRecord:
    return FlakRecord(
        test_id="test_x",
        run_id="r1",
        outcome=outcome,
        duration_s=1.0,
        error=error,
        timestamp="2026-04-25T00:00:00Z",
        worker=worker,
        environment="qa",
    )


@pytest.fixture()
def analyzer() -> PatternAnalyzer:
    llm = MagicMock()
    llm.complete.return_value = ""
    return PatternAnalyzer(llm=llm)


# ------------------------------------------------------------------
# Rule-based classification
# ------------------------------------------------------------------


def test_classify_timeout_error(analyzer: PatternAnalyzer) -> None:
    records = [_rec("failed", "TimeoutError: Timeout 30000ms exceeded.")] * 5
    pattern = analyzer.classify(_profile(), records, use_llm=False)
    assert pattern == FlakPattern.TIMING


def test_classify_waiting_for_locator(analyzer: PatternAnalyzer) -> None:
    records = [_rec("failed", "waiting for locator('[data-testid]') to be visible")] * 5
    pattern = analyzer.classify(_profile(), records, use_llm=False)
    assert pattern == FlakPattern.TIMING


def test_classify_environment_net_err(analyzer: PatternAnalyzer) -> None:
    records = [_rec("failed", "net::ERR_CONNECTION_REFUSED http://localhost:4200")] * 5
    pattern = analyzer.classify(_profile(), records, use_llm=False)
    assert pattern == FlakPattern.ENVIRONMENT


def test_classify_data_pollution_assertion_error(analyzer: PatternAnalyzer) -> None:
    records = [_rec("failed", "AssertionError: assert 'Dashboard' == 'Loading'")] * 5
    pattern = analyzer.classify(_profile(), records, use_llm=False)
    assert pattern == FlakPattern.DATA_POLLUTION


def test_classify_environment_wins_over_timeout(analyzer: PatternAnalyzer) -> None:
    # Environment signals take priority — a TimeoutError on a network call
    # is an environment issue, not a timing issue per se.
    records = [_rec("failed", "net::ERR_CONNECTION_REFUSED — TimeoutError")] * 5
    pattern = analyzer.classify(_profile(), records, use_llm=False)
    assert pattern == FlakPattern.ENVIRONMENT


def test_classify_no_errors_returns_order_dependent(analyzer: PatternAnalyzer) -> None:
    records = [_rec("failed", "")] * 5
    pattern = analyzer.classify(_profile(), records, use_llm=False)
    assert pattern == FlakPattern.ORDER_DEPENDENT


def test_classify_unknown_when_llm_disabled_and_no_signal(analyzer: PatternAnalyzer) -> None:
    records = [_rec("failed", "SomeRandomError: xyz")] * 5
    # No timing, env, data, or locator signals; no parallel workers; error text present
    # so ORDER_DEPENDENT heuristic (empty error) doesn't fire
    # Falls through to LLM if enabled, or UNKNOWN if disabled
    pattern = analyzer.classify(_profile(), records, use_llm=False)
    assert pattern in (FlakPattern.UNKNOWN, FlakPattern.ORDER_DEPENDENT)


# ------------------------------------------------------------------
# Parallel contention detection
# ------------------------------------------------------------------


def test_parallel_contention_detected(analyzer: PatternAnalyzer) -> None:
    records = (
        [_rec("failed", "SharedResource conflict", worker="gw0")] * 6
        + [_rec("passed", "", worker="gw0")] * 4
        + [_rec("passed", "", worker="main")] * 10
        + [_rec("failed", "SharedResource conflict", worker="main")] * 0
    )
    assert analyzer._is_parallel_contention(records) is True


def test_parallel_contention_not_detected_when_sequential_also_fails(
    analyzer: PatternAnalyzer,
) -> None:
    records = (
        [_rec("failed", "err", worker="gw0")] * 5
        + [_rec("passed", "", worker="gw0")] * 5
        + [_rec("failed", "err", worker="main")] * 5
        + [_rec("passed", "", worker="main")] * 5
    )
    assert analyzer._is_parallel_contention(records) is False


def test_parallel_contention_skipped_when_too_few_runs(analyzer: PatternAnalyzer) -> None:
    records = [_rec("failed", "", worker="gw0")] * 2
    assert analyzer._is_parallel_contention(records) is False


# ------------------------------------------------------------------
# LLM fallback
# ------------------------------------------------------------------


def test_llm_called_when_use_llm_true_and_no_rule_match() -> None:
    llm = MagicMock()
    llm.complete.return_value = "timing"
    analyzer = PatternAnalyzer(llm=llm)
    records = [_rec("failed", "SomeUnrecognisedError: xyz")] * 5
    pattern = analyzer.classify(_profile(), records, use_llm=True)
    llm.complete.assert_called_once()
    assert pattern == FlakPattern.TIMING


def test_llm_not_called_when_rule_matches() -> None:
    llm = MagicMock()
    analyzer = PatternAnalyzer(llm=llm)
    records = [_rec("failed", "TimeoutError: 30000ms exceeded")] * 5
    analyzer.classify(_profile(), records, use_llm=True)
    llm.complete.assert_not_called()


def test_llm_invalid_response_returns_unknown() -> None:
    llm = MagicMock()
    llm.complete.return_value = "completely invalid response"
    analyzer = PatternAnalyzer(llm=llm)
    records = [_rec("failed", "SomeRandomError")] * 5
    pattern = analyzer.classify(_profile(), records, use_llm=True)
    assert pattern == FlakPattern.UNKNOWN
