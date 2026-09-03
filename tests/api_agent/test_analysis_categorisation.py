"""Categorisation must read the failure, not the name of the test that failed.

When pytest gives no structured detail the fallback message is the ``FAILED
tests/x.py::test_get_user_404`` line itself, so anything scanning it for a
status code reads the scenario in the test's own name as evidence.
"""

from __future__ import annotations

import pytest

from api.agents.analysis import AnalysisAgent, FailureCategory
from api.agents.execution import ExecutionResult


def _categorise(test_name: str, message: str) -> FailureCategory:
    result = ExecutionResult(
        passed=0,
        failed=1,
        failure_details=[{"test_name": test_name, "message": message}],
    )
    return AnalysisAgent().analyze(result)[0].category


@pytest.mark.parametrize(
    "test_name",
    ["test_get_user_404", "test_post_returns_500", "test_timeout_handling"],
)
def test_scenario_named_test_is_not_categorised_from_its_own_name(test_name: str) -> None:
    category = _categorise(test_name, f"FAILED tests/api/test_users.py::{test_name}")
    assert category == FailureCategory.UNKNOWN


def test_test_name_does_not_override_the_real_cause() -> None:
    """A connection failure in a 404-named test is still a connection failure."""
    category = _categorise(
        "test_get_user_404",
        "FAILED tests/api/test_users.py::test_get_user_404 - "
        "ConnectionError: Failed to establish a new connection",
    )
    assert category == FailureCategory.ENV_ERROR


# --- Positive controls: real status codes must still categorise ---


@pytest.mark.parametrize(
    "message",
    [
        "Validation failed: expected status 200, got 404",
        "requests.HTTPError: 403 Client Error: Forbidden for url",
        "Unauthorized",
    ],
)
def test_real_4xx_evidence_is_still_an_api_error(message: str) -> None:
    assert _categorise("test_something", message) == FailureCategory.API_ERROR


def test_real_5xx_evidence_is_still_an_api_error() -> None:
    assert _categorise("test_x", "500 Internal Server Error") == FailureCategory.API_ERROR
