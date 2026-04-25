"""Tests for ValidationEngine — status, response time, JSON schema, headers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from api.engine.validation import ValidationEngine, ValidationResult, ValidationRules


def make_response(
    status_code: int = 200,
    json_body: dict | None = None,
    headers: dict | None = None,
    elapsed_ms: float = 100.0,
) -> MagicMock:
    """Build a mock requests.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.headers = headers or {"Content-Type": "application/json"}
    elapsed = MagicMock()
    elapsed.total_seconds.return_value = elapsed_ms / 1000
    mock_resp.elapsed = elapsed

    body = json_body or {}
    mock_resp.json.return_value = body
    mock_resp.text = json.dumps(body)
    return mock_resp


@pytest.fixture
def engine() -> ValidationEngine:
    return ValidationEngine()


# --- Status code ---


def test_correct_status_passes(engine: ValidationEngine) -> None:
    resp = make_response(status_code=200)
    rules = ValidationRules(expected_status=200)
    result = engine.validate(resp, rules)
    assert result.passed
    assert result.failures == []


def test_wrong_status_fails(engine: ValidationEngine) -> None:
    resp = make_response(status_code=404)
    rules = ValidationRules(expected_status=200)
    result = engine.validate(resp, rules)
    assert not result.passed
    assert any("404" in f for f in result.failures)


def test_no_status_rule_always_passes(engine: ValidationEngine) -> None:
    resp = make_response(status_code=500)
    rules = ValidationRules()
    result = engine.validate(resp, rules)
    assert result.passed


# --- Response time ---


def test_within_time_passes(engine: ValidationEngine) -> None:
    resp = make_response(elapsed_ms=200)
    rules = ValidationRules(max_response_time_ms=1000)
    result = engine.validate(resp, rules, duration_ms=200)
    assert result.passed


def test_exceeds_time_fails(engine: ValidationEngine) -> None:
    resp = make_response(elapsed_ms=2000)
    rules = ValidationRules(max_response_time_ms=500)
    result = engine.validate(resp, rules, duration_ms=2000)
    assert not result.passed
    assert any("2000" in f or "exceeds" in f for f in result.failures)


def test_elapsed_read_from_response_when_not_passed(engine: ValidationEngine) -> None:
    resp = make_response(elapsed_ms=50)
    rules = ValidationRules(max_response_time_ms=1000)
    # Do not pass duration_ms — engine must use response.elapsed
    result = engine.validate(resp, rules)
    assert result.passed
    assert result.duration_ms == pytest.approx(50.0, abs=1.0)


# --- JSON schema ---


def test_valid_schema_passes(engine: ValidationEngine) -> None:
    schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    resp = make_response(json_body={"id": 1, "name": "Alice"})
    rules = ValidationRules(json_schema=schema)
    result = engine.validate(resp, rules)
    assert result.passed, result.failures


def test_schema_violation_fails(engine: ValidationEngine) -> None:
    schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }
    resp = make_response(json_body={"name": "Alice"})  # missing required "id"
    rules = ValidationRules(json_schema=schema)
    result = engine.validate(resp, rules)
    assert not result.passed
    assert any("schema" in f.lower() for f in result.failures)


def test_non_json_response_fails_schema(engine: ValidationEngine) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    elapsed = MagicMock()
    elapsed.total_seconds.return_value = 0.1
    mock_resp.elapsed = elapsed
    mock_resp.json.side_effect = ValueError("not JSON")

    rules = ValidationRules(json_schema={"type": "object"})
    result = engine.validate(mock_resp, rules)
    assert not result.passed
    assert any("JSON" in f for f in result.failures)


# --- Required headers ---


def test_required_header_present_passes(engine: ValidationEngine) -> None:
    resp = make_response(headers={"Content-Type": "application/json; charset=utf-8"})
    rules = ValidationRules(required_headers={"Content-Type": "application/json"})
    result = engine.validate(resp, rules)
    assert result.passed


def test_required_header_missing_fails(engine: ValidationEngine) -> None:
    resp = make_response(headers={"Content-Type": "text/html"})
    rules = ValidationRules(required_headers={"Content-Type": "application/json"})
    result = engine.validate(resp, rules)
    assert not result.passed
    assert any("Content-Type" in f for f in result.failures)


# --- Multiple rules ---


def test_multiple_rules_all_pass(engine: ValidationEngine) -> None:
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    resp = make_response(
        status_code=200,
        json_body={"ok": True},
        headers={"Content-Type": "application/json"},
        elapsed_ms=100,
    )
    rules = ValidationRules(
        expected_status=200,
        max_response_time_ms=1000,
        json_schema=schema,
        required_headers={"Content-Type": "application/json"},
    )
    result = engine.validate(resp, rules, duration_ms=100)
    assert result.passed
    assert result.failures == []


def test_multiple_failures_accumulated(engine: ValidationEngine) -> None:
    resp = make_response(status_code=404, elapsed_ms=3000)
    rules = ValidationRules(
        expected_status=200,
        max_response_time_ms=500,
    )
    result = engine.validate(resp, rules, duration_ms=3000)
    assert not result.passed
    assert len(result.failures) == 2


# --- ValidationResult dataclass ---


def test_validation_result_defaults() -> None:
    r = ValidationResult(passed=True)
    assert r.failures == []
    assert r.duration_ms == 0.0


def test_validation_result_with_failures() -> None:
    r = ValidationResult(passed=False, failures=["err1", "err2"], duration_ms=300.0)
    assert not r.passed
    assert len(r.failures) == 2
    assert r.duration_ms == 300.0
