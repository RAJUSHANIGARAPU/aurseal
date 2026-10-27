"""Tests for verification.boundary: the refusal control runs first and gates the acceptance."""

from __future__ import annotations

import re

import pytest

from api.clients.http_engine import ApiResponse
from verification.boundary import assert_boundary, matches, refusal_text
from verification.verdicts import CoverageOutcome, read_verdicts

RULE = "at most 500 seats"


def _refused(text: str = RULE) -> ApiResponse:
    return ApiResponse(422, f'{{"message": "Plans hold {text}."}}'.encode())


def test_proves_when_the_sibling_is_refused_by_the_rule_and_the_boundary_accepted():
    calls: list[str] = []

    def refuse():
        calls.append("refuse")
        return _refused()

    def accept():
        calls.append("accept")
        return ApiResponse(201, b"{}")

    evidence = assert_boundary(
        accept, refuse, refusal_matches=[RULE], accepted_value=500, refused_value=501
    )
    assert calls == ["refuse", "accept"], "the control must run before the acceptance"
    assert "500 accepted" in evidence and "501 refused" in evidence and RULE in evidence
    assert read_verdicts()[-1]["verdict"] == CoverageOutcome.PROVED.value


def test_abstains_when_the_sibling_is_accepted():
    accepted: list[int] = []
    with pytest.raises(pytest.skip.Exception, match="boundary control did not refuse"):
        assert_boundary(
            lambda: accepted.append(1),
            lambda: ApiResponse(201, b"{}"),
            refusal_matches=[RULE],
            refused_value=501,
        )
    assert accepted == [], "the acceptance must not run without a working control"


def test_abstains_when_the_refusal_is_another_rule():
    with pytest.raises(pytest.skip.Exception, match="expected rule"):
        assert_boundary(
            lambda: True, lambda: "Name is required.", refusal_matches=[re.compile(r"\b500\b")]
        )


def test_fails_when_the_boundary_value_is_refused():
    with pytest.raises(AssertionError, match="boundary value 500 was refused"):
        assert_boundary(
            lambda: _refused(),
            lambda: _refused(),
            refusal_matches=[RULE],
            accepted_value=500,
            refused_value=501,
        )


def test_accepts_bool_and_captured_message_outcomes():
    assert_boundary(lambda: True, lambda: f"Plans hold {RULE}.", refusal_matches=[RULE])
    with pytest.raises(AssertionError):
        assert_boundary(lambda: False, lambda: RULE, refusal_matches=[RULE])


def test_helpers():
    assert refusal_text(None) is None
    assert refusal_text("  ") is None
    assert refusal_text(ApiResponse(200)) is None
    assert refusal_text(ApiResponse(400, b"bad")) == "HTTP 400 bad"
    with pytest.raises(TypeError):
        refusal_text(3.5)
    assert matches("x at most 500 seats", ["nope", RULE]) == RULE
    assert matches("abc", [re.compile("z")]) is None
    with pytest.raises(ValueError):
        assert_boundary(lambda: True, lambda: RULE, refusal_matches=[])
