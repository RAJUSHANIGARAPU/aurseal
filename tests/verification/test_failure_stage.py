"""Tests for the failure-stage classifier, pinned on recorded answers."""

from __future__ import annotations

import pytest

from api.clients.http_engine import ApiResponse
from verification.evidence_verdict import HarnessError
from verification.outcome_classifier import OTHER, StageRule, probe_stage, stage_of

STAGES = [
    StageRule("LOOKUP", frozenset({404}), ("account not found",)),
    StageRule("CREDENTIAL", frozenset({401, 403}), ("invalid credential",)),
    StageRule("LOCKED", frozenset({423})),
]

#: Recorded answers of a chain: lookup first, then the credential check.
RECORDED = {
    "unknown account": (404, '{"title": "Account not found", "status": 404}'),
    "known account, bad secret": (401, '{"detail": "Invalid credential supplied"}'),
    "known account, locked": (423, ""),
    "gateway page": (502, "<html>Bad gateway</html>"),
}


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("unknown account", "LOOKUP"),
        ("known account, bad secret", "CREDENTIAL"),
        ("known account, locked", "LOCKED"),
        ("gateway page", OTHER),
    ],
)
def test_recorded_answers_classify(case, expected):
    status, body = RECORDED[case]
    stage = stage_of(status, body, STAGES)
    assert stage.name == expected
    assert stage.known is (expected != OTHER)


def test_status_alone_is_not_enough_when_the_rule_names_text():
    assert stage_of(404, '{"error": "no_route"}', STAGES).name == OTHER


def test_probe_returns_the_stage_of_a_refusal():
    status, body = RECORDED["known account, bad secret"]
    stage = probe_stage(lambda: ApiResponse(status, body.encode()), STAGES)
    assert str(stage) == "CREDENTIAL (HTTP 401)"


def test_a_granted_probe_is_a_harness_error():
    with pytest.raises(HarnessError, match="granted"):
        probe_stage(lambda: ApiResponse(200, b'{"token": "x"}'), STAGES)


def test_rules_are_validated():
    with pytest.raises(ValueError):
        StageRule(OTHER, frozenset({400}))
    with pytest.raises(TypeError):
        probe_stage(lambda: "nope", STAGES)
