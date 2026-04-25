"""Tests for memory.summarizer.MemorySummarizer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from memory.summarizer import MemorySummarizer


class FailureCategory(str, Enum):
    ASSERTION_ERROR = "ASSERTION_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass
class FakeFailureAnalysis:
    test_name: str = "test_something"
    category: FailureCategory = FailureCategory.ASSERTION_ERROR
    root_cause: str = "Expected 200, got 404"
    suggested_fix: str = "Check endpoint"
    raw_message: str = "AssertionError: expected 200 got 404"
    llm_diagnosis: dict = field(default_factory=dict)


@dataclass
class FakeRequest:
    name: str = "Get Users"
    method: str = "GET"
    url: str = "https://api.example.com/users/123"
    body: dict | None = field(default_factory=lambda: {"filter": "active"})
    folder_path: list = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    query_params: dict = field(default_factory=dict)
    body_mode: str = "json"
    pre_request_script: str = ""
    test_script: str = ""


# ------------------------------------------------------------------
# Endpoint normalization
# ------------------------------------------------------------------


def test_normalize_endpoint_strips_query_params():
    url = "https://api.example.com/users?page=2&limit=10"
    result = MemorySummarizer.normalize_endpoint(url)
    assert "?" not in result
    assert "page" not in result


def test_normalize_endpoint_strips_numeric_ids():
    url = "https://api.example.com/users/42/orders"
    result = MemorySummarizer.normalize_endpoint(url)
    assert "/42" not in result
    assert "{id}" in result


def test_normalize_endpoint_strips_uuids():
    url = "https://api.example.com/items/550e8400-e29b-41d4-a716-446655440000"
    result = MemorySummarizer.normalize_endpoint(url)
    assert "550e8400" not in result
    assert "{id}" in result


def test_normalize_endpoint_lowercases():
    result = MemorySummarizer.normalize_endpoint("https://API.EXAMPLE.COM/USERS")
    assert result == result.lower()


def test_normalize_endpoint_strips_trailing_slash():
    result = MemorySummarizer.normalize_endpoint("https://api.example.com/users/")
    assert not result.endswith("/")


# ------------------------------------------------------------------
# Error normalization
# ------------------------------------------------------------------


def test_normalize_error_strips_timestamps():
    error = "2026-04-25T14:30:22.123Z Error occurred in module"
    result = MemorySummarizer.normalize_error(error)
    assert "2026-04-25" not in result
    assert "Error occurred" in result


def test_normalize_error_strips_memory_addresses():
    error = "Object at 0x7f3a1b2c failed"
    result = MemorySummarizer.normalize_error(error)
    assert "0x7f3a1b2c" not in result


def test_normalize_error_truncates_at_500():
    long_error = "x" * 1000
    result = MemorySummarizer.normalize_error(long_error)
    assert len(result) <= 500


def test_normalize_error_empty_string():
    assert MemorySummarizer.normalize_error("") == ""


# ------------------------------------------------------------------
# from_failure_analysis
# ------------------------------------------------------------------


def test_from_failure_analysis_populates_fields():
    s = MemorySummarizer()
    fa = FakeFailureAnalysis()
    req = FakeRequest()
    record = s.from_failure_analysis(fa, req, run_id="run_001")
    assert record.test_id == "test_something"
    assert record.category == "ASSERTION_ERROR"
    assert record.method == "GET"
    assert record.fix_outcome == "pending"


def test_from_failure_analysis_normalizes_endpoint():
    s = MemorySummarizer()
    fa = FakeFailureAnalysis()
    req = FakeRequest(url="https://api.example.com/users/99/orders")
    record = s.from_failure_analysis(fa, req, run_id="run_001")
    assert "99" not in record.endpoint


def test_from_failure_analysis_handles_none_request():
    s = MemorySummarizer()
    fa = FakeFailureAnalysis()
    record = s.from_failure_analysis(fa, None, run_id="run_001")
    assert record.endpoint == ""
    assert record.method == ""


def test_from_failure_analysis_truncates_payload():
    s = MemorySummarizer()
    fa = FakeFailureAnalysis()
    req = FakeRequest(body={"data": "x" * 1000})
    record = s.from_failure_analysis(fa, req, run_id="run_001")
    assert len(record.payload_snippet) <= 200


def test_from_failure_analysis_uses_environment():
    s = MemorySummarizer()
    fa = FakeFailureAnalysis()
    record = s.from_failure_analysis(fa, None, run_id="r", environment="staging")
    assert record.environment == "staging"


# ------------------------------------------------------------------
# from_success
# ------------------------------------------------------------------


def test_from_success_sets_resolved_outcome():
    s = MemorySummarizer()
    record = s.from_success("test_ok", None, run_id="r")
    assert record.fix_outcome == "resolved"
    assert record.category == "success"


# ------------------------------------------------------------------
# from_pytest_report
# ------------------------------------------------------------------


def test_from_pytest_report_failure():
    s = MemorySummarizer()
    record = s.from_pytest_report(
        node_id="tests/test_foo.py::test_bar",
        outcome="failed",
        error="AssertionError: assert 1 == 2",
        run_id="r",
    )
    assert record.fix_outcome == "pending"
    assert record.category == "failure"
    assert "AssertionError" in record.error_signature


def test_from_pytest_report_passed():
    s = MemorySummarizer()
    record = s.from_pytest_report(
        node_id="tests/test_foo.py::test_bar",
        outcome="passed",
        error="",
        run_id="r",
    )
    assert record.fix_outcome == "resolved"
    assert record.error_signature == ""
