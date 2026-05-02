"""Tests for contract_testing.reporter."""

from __future__ import annotations

import json

import pytest

from contract_testing.models import ContractDiff, ValidationResult
from contract_testing.reporter import ContractReporter


@pytest.fixture()
def reporter(tmp_path):
    return ContractReporter(output_dir=tmp_path / "reports")


def _diff(breaking=None, non_breaking=None) -> ContractDiff:
    d = ContractDiff()
    d.breaking = breaking or []
    d.non_breaking = non_breaking or []
    return d


def _result(key: str, passed: bool, errors=None) -> ValidationResult:
    return ValidationResult(interaction_key=key, passed=passed, errors=errors or [])


# ------------------------------------------------------------------
# write_diff_report
# ------------------------------------------------------------------


def test_write_diff_report_creates_file(reporter, tmp_path):
    diff = _diff(breaking=["field 'name' removed"])
    path = reporter.write_diff_report("c", "p", "1.0.0", "2.0.0", diff)
    assert path.exists()


def test_write_diff_report_content(reporter):
    diff = _diff(breaking=["field 'name' removed"], non_breaking=["new field 'email'"])
    path = reporter.write_diff_report("c", "p", "1.0.0", "2.0.0", diff)
    data = json.loads(path.read_text())
    assert data["consumer"] == "c"
    assert data["provider"] == "p"
    assert data["old_version"] == "1.0.0"
    assert data["new_version"] == "2.0.0"
    assert data["change_type"] == "breaking"
    assert len(data["breaking_changes"]) == 1
    assert len(data["non_breaking_changes"]) == 1


def test_write_diff_report_no_changes(reporter):
    diff = _diff()
    path = reporter.write_diff_report("c", "p", "1.0.0", "1.0.1", diff)
    data = json.loads(path.read_text())
    assert data["change_type"] == "none"


def test_write_diff_report_filename_contains_consumer_provider(reporter):
    diff = _diff()
    path = reporter.write_diff_report("consumer", "provider", "1.0.0", "1.0.1", diff)
    assert "consumer" in path.name
    assert "provider" in path.name


# ------------------------------------------------------------------
# write_validation_report
# ------------------------------------------------------------------


def test_write_validation_report_creates_file(reporter):
    results = [_result("GET /users", True)]
    path = reporter.write_validation_report("c", "p", results)
    assert path.exists()


def test_write_validation_report_summary_counts(reporter):
    results = [
        _result("GET /users", True),
        _result("POST /users", False, ["missing 'id'"]),
        _result("DELETE /users/1", False, ["status mismatch"]),
    ]
    path = reporter.write_validation_report("c", "p", results)
    data = json.loads(path.read_text())
    assert data["summary"]["total"] == 3
    assert data["summary"]["passed"] == 1
    assert data["summary"]["failed"] == 2


def test_write_validation_report_failures_listed(reporter):
    results = [_result("GET /users", False, ["'name' is required"])]
    path = reporter.write_validation_report("c", "p", results)
    data = json.loads(path.read_text())
    assert data["failures"][0]["interaction"] == "GET /users"
    assert "'name' is required" in data["failures"][0]["errors"]


def test_write_validation_report_all_pass_no_failures(reporter):
    results = [_result("GET /users", True), _result("POST /orders", True)]
    path = reporter.write_validation_report("c", "p", results)
    data = json.loads(path.read_text())
    assert data["failures"] == []


# ------------------------------------------------------------------
# generate_session_summary
# ------------------------------------------------------------------


def test_session_summary_all_passed(reporter):
    results = [_result("GET /users", True), _result("POST /orders", True)]
    summary = reporter.generate_session_summary("c", "p", 1, results)
    assert "All" in summary
    assert "passed" in summary


def test_session_summary_with_failures(reporter):
    results = [_result("GET /users", False, ["error"]), _result("POST /orders", True)]
    summary = reporter.generate_session_summary("c", "p", 1, results)
    assert "FAILED" in summary


def test_session_summary_zero_contracts(reporter):
    summary = reporter.generate_session_summary("c", "p", 0, [])
    assert "0" in summary


def test_session_summary_multiple_contracts_saved(reporter):
    summary = reporter.generate_session_summary("c", "p", 3, [])
    assert "3" in summary
