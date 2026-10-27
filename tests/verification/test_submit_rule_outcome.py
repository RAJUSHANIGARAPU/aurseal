"""A refusal at submit: refused, unattributed, accepted, or no verdict, never a false accept."""

from __future__ import annotations

import pytest

from ui.components.submit_outcome import SubmitResult
from verification.evidence_verdict import EvidenceVerdict
from verification.outcome_classifier import SubmitRuleState, submit_rule_outcome

MESSAGE = "Seat count cannot exceed 500."


@pytest.mark.parametrize(
    ("result", "state"),
    [
        (SubmitResult(422, body=MESSAGE, advanced=False), SubmitRuleState.REFUSED_AT_SUBMIT),
        (SubmitResult(200, inline_errors=[MESSAGE]), SubmitRuleState.REFUSED_AT_SUBMIT),
        (
            SubmitResult(422, body="other", advanced=False),
            SubmitRuleState.REFUSED_WITHOUT_THE_MESSAGE,
        ),
        (SubmitResult(200, advanced=True), SubmitRuleState.ACCEPTED),
        (SubmitResult(201), SubmitRuleState.ACCEPTED),
        (SubmitResult(None), SubmitRuleState.NO_VERDICT_OUTCOME_UNKNOWN),
        (
            SubmitResult.not_exercised("required input(s) still empty: Billing contact"),
            SubmitRuleState.NOT_EXERCISED,
        ),
    ],
)
def test_each_outcome(result, state):
    assert submit_rule_outcome(result, MESSAGE).state is state


def test_an_unmet_precondition_is_an_abstention_never_accepted():
    verdict = submit_rule_outcome(SubmitResult.not_exercised("probe value gone"), MESSAGE)
    assert not verdict.is_verdict and not verdict.passed
    with pytest.raises(pytest.skip.Exception, match="NOT_EXERCISED"):
        verdict.report(EvidenceVerdict("seat cap refusal"))


def test_without_an_expected_message_any_refusal_counts():
    assert submit_rule_outcome(SubmitResult(400)).state is SubmitRuleState.REFUSED_AT_SUBMIT


def test_accepted_fails_with_the_observations():
    verdict = submit_rule_outcome(SubmitResult(200, advanced=True), MESSAGE)
    with pytest.raises(AssertionError, match="ACCEPTED.*status=200"):
        verdict.report(EvidenceVerdict("seat cap refusal"))
