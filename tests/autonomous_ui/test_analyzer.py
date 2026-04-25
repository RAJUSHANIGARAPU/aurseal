"""Tests for autonomous_ui.analyzer — failure classification and hint extraction."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from autonomous_ui.analyzer import FailureAnalyzer
from autonomous_ui.models import FailureBundle, FailureType


def _bundle(error: str = "", stack: str = "", dom: str = "") -> FailureBundle:
    return FailureBundle(
        test="test_example",
        timestamp="2026-04-25T10:00:00Z",
        error=error,
        stack_trace=stack,
        screenshot="",
        console_errors=[],
        failed_requests=[],
        dom_snapshot=dom,
    )


@pytest.fixture()
def analyzer() -> FailureAnalyzer:
    llm = MagicMock()
    llm.complete.return_value = ""
    return FailureAnalyzer(llm=llm)


# ------------------------------------------------------------------
# Failure type classification
# ------------------------------------------------------------------


def test_classify_timeout(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(error="TimeoutError: locator.click: Timeout 30000ms exceeded.")
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.failure_type == FailureType.TIMEOUT


def test_classify_waiting_for_locator(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(stack="call log: waiting for locator('[data-testid=\"submit\"]')")
    result = analyzer.analyze(bundle, use_llm=False)
    # "waiting for locator" appears in stack but no TimeoutError keyword → LOCATOR
    # However if Playwright raises TimeoutError for a waiting locator, TIMEOUT wins.
    # Here error is empty so LOCATOR is the match.
    assert result.failure_type == FailureType.LOCATOR


def test_classify_strict_mode_violation(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(error="Error: strict mode violation: locator('button') resolved to 3 elements")
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.failure_type == FailureType.LOCATOR


def test_classify_assertion_error(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(error="AssertionError: assert 'Loading...' == 'Dashboard'")
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.failure_type == FailureType.ASSERTION


def test_classify_navigation_error(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(error="net::ERR_CONNECTION_REFUSED http://localhost:4200")
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.failure_type == FailureType.NAVIGATION


def test_classify_unknown_error(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(error="RuntimeError: something unexpected happened")
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.failure_type == FailureType.UNKNOWN


def test_timeout_takes_priority_over_locator_signal(analyzer: FailureAnalyzer) -> None:
    # A timed-out locator wait shows both "TimeoutError" and "waiting for locator"
    bundle = _bundle(
        error="TimeoutError: locator.click: Timeout 30000ms exceeded.",
        stack="waiting for locator('[data-testid=\"btn\"]')",
    )
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.failure_type == FailureType.TIMEOUT


# ------------------------------------------------------------------
# Selector extraction
# ------------------------------------------------------------------


def test_extracts_selector_from_error(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(
        error="Error: waiting for locator('[data-testid=\"search-field\"]')",
        stack="locator('[data-testid=\"search-field\"]') call log",
    )
    result = analyzer.analyze(bundle, use_llm=False)
    assert '[data-testid="search-field"]' in result.selectors_mentioned


def test_deduplicates_selectors(analyzer: FailureAnalyzer) -> None:
    sel = '[data-testid="btn"]'
    bundle = _bundle(error=f"locator('{sel}') ... locator('{sel}')")
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.selectors_mentioned.count(sel) == 1


def test_no_selectors_when_none_in_error(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(error="AssertionError: assert '' == 'Dashboard'")
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.selectors_mentioned == []


# ------------------------------------------------------------------
# Confidence and root cause
# ------------------------------------------------------------------


def test_high_confidence_for_locator(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(error="Error: waiting for locator('button')")
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.confidence == "high"


def test_high_confidence_for_timeout(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(error="TimeoutError: Timeout 30000ms exceeded.")
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.confidence == "high"


def test_medium_confidence_for_assertion(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(error="AssertionError: assert 'x' == 'y'")
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.confidence == "medium"


def test_low_confidence_for_unknown(analyzer: FailureAnalyzer) -> None:
    bundle = _bundle(error="Something exploded")
    result = analyzer.analyze(bundle, use_llm=False)
    assert result.confidence == "low"


# ------------------------------------------------------------------
# LLM integration
# ------------------------------------------------------------------


def test_llm_called_when_use_llm_true() -> None:
    llm = MagicMock()
    llm.complete.return_value = (
        "ROOT CAUSE: selector stale\nHEALING SUGGESTION: use data-testid\nCONFIDENCE: high"  # noqa: E501
    )
    analyzer = FailureAnalyzer(llm=llm)
    bundle = _bundle(error="TimeoutError: Timeout 30000ms exceeded.")
    result = analyzer.analyze(bundle, use_llm=True)
    llm.complete.assert_called_once()
    assert result.llm_suggestion != ""


def test_llm_not_called_when_use_llm_false() -> None:
    llm = MagicMock()
    analyzer = FailureAnalyzer(llm=llm)
    bundle = _bundle(error="TimeoutError: Timeout 30000ms exceeded.")
    analyzer.analyze(bundle, use_llm=False)
    llm.complete.assert_not_called()


def test_llm_prompt_includes_dom_when_present() -> None:
    llm = MagicMock()
    llm.complete.return_value = ""
    analyzer = FailureAnalyzer(llm=llm)
    bundle = _bundle(error="Error: strict mode violation", dom="<html><body>test</body></html>")
    analyzer.analyze(bundle, use_llm=True)
    prompt_sent = llm.complete.call_args[0][0]
    assert "DOM SNAPSHOT" in prompt_sent


def test_llm_prompt_omits_dom_section_when_empty() -> None:
    llm = MagicMock()
    llm.complete.return_value = ""
    analyzer = FailureAnalyzer(llm=llm)
    bundle = _bundle(error="Error: strict mode violation", dom="")
    analyzer.analyze(bundle, use_llm=True)
    prompt_sent = llm.complete.call_args[0][0]
    assert "DOM SNAPSHOT" not in prompt_sent


# ------------------------------------------------------------------
# FailureBundle.from_dict
# ------------------------------------------------------------------


def test_bundle_from_dict_maps_fields() -> None:
    data = {
        "test": "test_login",
        "timestamp": "2026-04-25T00:00:00Z",
        "error": "AssertionError",
        "stackTrace": "traceback...",
        "screenshot": "abc123",
        "consoleErrors": ["TypeError: null"],
        "failedRequests": ["POST /api → 401"],
        "domSnapshot": "<html/>",
    }
    bundle = FailureBundle.from_dict(data)
    assert bundle.test == "test_login"
    assert bundle.stack_trace == "traceback..."
    assert bundle.dom_snapshot == "<html/>"


def test_bundle_from_dict_defaults_missing_fields() -> None:
    bundle = FailureBundle.from_dict({})
    assert bundle.test == ""
    assert bundle.dom_snapshot == ""
    assert bundle.console_errors == []
