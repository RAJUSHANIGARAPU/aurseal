"""Outcome classifiers: every branch, the order between them, and the routing of abstentions."""

from __future__ import annotations

from dataclasses import replace

import pytest

from verification.evidence_verdict import EvidenceVerdict
from verification.outcome_classifier import (
    DEFAULT_REFUSAL_MESSAGE,
    OutcomeState,
    PriceProbe,
    PriceSurfaceClassifier,
    PriceSurfaceExpectation,
    PriceSurfaceObservations,
    PriceSurfaceState,
    RefusalClassifier,
    RefusalObservations,
    RefusalState,
    StepObservations,
    StepState,
    Verdict,
    expected_step_state,
    join_block_values,
    refusal_outcome,
    step_outcome,
)
from verification.verdicts import read_verdicts

EXPECT = PriceSurfaceExpectation()


# ---------------------------------------------------------------------------
# base
# ---------------------------------------------------------------------------


class _Flags(OutcomeState):
    GOOD = (True, True)
    BAD_A = (True, False)
    BAD_B = (True, False)
    NONE = (False, False)


def test_states_with_equal_flags_stay_distinct_members():
    assert _Flags.BAD_A is not _Flags.BAD_B
    assert len(list(_Flags)) == 4
    assert _Flags.BAD_A.demands_follow_up and not _Flags.GOOD.demands_follow_up
    assert not _Flags.NONE.demands_follow_up


def test_report_routes_pass_to_proved(isolated_verdicts):
    Verdict(_Flags.GOOD, "observed 3 of 3").report(EvidenceVerdict("REQ-7 guard"))
    (record,) = read_verdicts(isolated_verdicts)
    assert record["verdict"] == "proved" and "GOOD: observed 3 of 3" in record["detail"]


def test_report_routes_abstention_to_no_verdict(isolated_verdicts):
    with pytest.raises(pytest.skip.Exception, match="NONE: never reached"):
        Verdict(_Flags.NONE, "never reached").report(EvidenceVerdict("REQ-7 guard"))
    (record,) = read_verdicts(isolated_verdicts)
    assert record["verdict"] == "abstained"


def test_report_abstention_fails_when_a_verdict_is_required(monkeypatch):
    monkeypatch.setenv("COVERAGE_VERDICT_REQUIRED", "true")
    with pytest.raises(pytest.fail.Exception, match="NO VERDICT"):
        Verdict(_Flags.NONE, "never reached").report(EvidenceVerdict("REQ-7 guard"))


def test_report_product_failure_raises_the_worded_message(isolated_verdicts):
    with pytest.raises(AssertionError, match="BAD_A: the guard stayed silent"):
        Verdict(_Flags.BAD_A, "the guard stayed silent").report(EvidenceVerdict("REQ-7 guard"))
    assert read_verdicts(isolated_verdicts) == []


# ---------------------------------------------------------------------------
# price surface characterisation
# ---------------------------------------------------------------------------


def _probe(**overrides) -> PriceProbe:
    base = dict(
        attempted=True,
        arbitrary_price="137.50",
        arbitrary_accepted=True,
        zero_attempted=True,
        zero_blocked=True,
        zero_messages=(EXPECT.zero_price_refusal,),
    )
    base.update(overrides)
    return PriceProbe(**base)


def _surface(**overrides) -> PriceSurfaceObservations:
    base = dict(
        screen_reached=True,
        plan_editable=False,
        plan_allowed_values=("Standard",),
        add_on_labels=EXPECT.add_on_labels,
        other_field_labels=("Notes",),
        probe=_probe(),
    )
    base.update(overrides)
    return PriceSurfaceObservations(**base)


def _price(o: PriceSurfaceObservations):
    return PriceSurfaceClassifier().classify(o)


def test_characterisation_holds_on_the_pinned_screen():
    verdict = _price(_surface())
    assert verdict.state is PriceSurfaceState.NO_PRICE_SURFACE
    assert verdict.passed and verdict.is_verdict and not verdict.demands_follow_up
    assert "137.50" in verdict.message and verdict.observations is not None


@pytest.mark.parametrize(
    ("overrides", "state"),
    [
        ({"screen_reached": False}, PriceSurfaceState.SCREEN_NOT_REACHED),
        ({"plan_allowed_values": ()}, PriceSurfaceState.BLOCK_NOT_READ),
        ({"probe": PriceProbe()}, PriceSurfaceState.PRICE_PROBE_NOT_RUN),
        ({"probe": _probe(zero_attempted=False)}, PriceSurfaceState.PRICE_PROBE_NOT_RUN),
    ],
)
def test_runs_that_saw_too_little_abstain(overrides, state):
    verdict = _price(_surface(**overrides))
    assert verdict.state is state
    assert not verdict.is_verdict and not verdict.passed


def test_a_none_probe_counts_as_not_run():
    assert _price(_surface(probe=None)).state is PriceSurfaceState.PRICE_PROBE_NOT_RUN


@pytest.mark.parametrize(
    "overrides",
    [
        {"plan_allowed_values": ("Standard", "Premium")},
        {"other_field_labels": ("Price tier",)},
        {"other_field_labels": ("Rate-table",)},
        {"add_on_labels": (*EXPECT.add_on_labels, "Price list 2026")},
    ],
)
def test_a_new_price_surface_is_the_headline(overrides):
    verdict = _price(_surface(**overrides))
    assert verdict.state is PriceSurfaceState.PRICE_SURFACE_APPEARED
    assert verdict.demands_follow_up and "PREMISE IS STALE" in verdict.message


def test_price_surface_outranks_every_price_outcome():
    verdict = _price(_surface(other_field_labels=("Tier",), probe=_probe(zero_blocked=False)))
    assert verdict.state is PriceSurfaceState.PRICE_SURFACE_APPEARED


def test_documented_lookalikes_are_never_a_signal():
    verdict = _price(_surface(other_field_labels=("Schema version", "Quote-version", "VERSION")))
    assert verdict.state is PriceSurfaceState.NO_PRICE_SURFACE


def test_lookalike_list_is_configurable():
    classifier = PriceSurfaceClassifier(PriceSurfaceExpectation(lookalikes=()))
    verdict = classifier.classify(_surface(other_field_labels=("Schema version",)))
    assert verdict.state is PriceSurfaceState.PRICE_SURFACE_APPEARED


@pytest.mark.parametrize(
    ("probe", "state"),
    [
        (_probe(arbitrary_accepted=False), PriceSurfaceState.ARBITRARY_PRICE_REFUSED),
        (_probe(zero_blocked=False, zero_messages=()), PriceSurfaceState.ZERO_PRICE_ACCEPTED),
        (_probe(zero_blocked=False), PriceSurfaceState.ZERO_PRICE_REFUSAL_DID_NOT_BLOCK),
        (
            _probe(zero_messages=("Field is required.",)),
            PriceSurfaceState.ZERO_PRICE_REFUSED_BY_SOMETHING_ELSE,
        ),
        (
            _probe(zero_messages=("Price must be greater than zero.",)),
            PriceSurfaceState.ZERO_PRICE_REFUSED_BY_SOMETHING_ELSE,
        ),
    ],
)
def test_price_pair_outcomes(probe, state):
    verdict = _price(_surface(probe=probe))
    assert verdict.state is state
    assert verdict.is_verdict and not verdict.passed


def test_floor_message_is_compared_after_trimming_only():
    verdict = _price(_surface(probe=_probe(zero_messages=(f"  {EXPECT.zero_price_refusal} ",))))
    assert verdict.state is PriceSurfaceState.NO_PRICE_SURFACE


@pytest.mark.parametrize(
    "overrides",
    [
        {"plan_editable": True},
        {"plan_allowed_values": ("Basic",)},
        {"add_on_labels": ("Extended warranty", "Gift wrap")},
    ],
)
def test_drift_is_decided_last(overrides):
    verdict = _price(_surface(**overrides))
    assert verdict.state is PriceSurfaceState.DESCRIPTION_OUTDATED
    assert _price(_surface(**overrides, probe=_probe(zero_blocked=False))).state is (
        PriceSurfaceState.ZERO_PRICE_REFUSAL_DID_NOT_BLOCK
    )


def test_plan_value_match_ignores_case_and_punctuation():
    assert _price(_surface(plan_allowed_values=(" standard ",))).passed


# ---------------------------------------------------------------------------
# required block x step advance
# ---------------------------------------------------------------------------


def _step(**overrides) -> StepObservations:
    base = dict(
        case="postal",
        step_rendered=True,
        fields_present=4,
        block_value="1 Example Street",
        submit_clicked=True,
        submit_status=200,
        advanced=True,
    )
    base.update(overrides)
    return StepObservations(**base)


@pytest.mark.parametrize(
    ("overrides", "state"),
    [
        ({}, StepState.FILLED_AND_ADVANCED),
        ({"block_value": ""}, StepState.EMPTY_BUT_ADVANCED),
        (
            {"advanced": False, "submit_status": 422, "rejection_message_shown": True},
            StepState.FILLED_BUT_BLOCKED,
        ),
        (
            {"block_value": "", "advanced": False, "rejection_message_shown": True},
            StepState.EMPTY_AND_BLOCKED,
        ),
        (
            {"block_value": "", "advanced": False, "submit_status": 400},
            StepState.EMPTY_AND_BLOCKED_WITHOUT_THE_MESSAGE,
        ),
        (
            {"block_value": "", "advanced": False, "validation_surfaces": ["Required"]},
            StepState.EMPTY_AND_BLOCKED_WITHOUT_THE_MESSAGE,
        ),
        ({"step_rendered": False}, StepState.NO_VERDICT_STEP_NEVER_RENDERED),
        ({"fields_present": 0, "block_value": ""}, StepState.NO_VERDICT_BLOCK_ABSENT),
        ({"submit_clicked": False}, StepState.NO_VERDICT_SUBMIT_NOT_PRESSED),
        (
            {"block_value": "", "advanced": False, "submit_status": -1},
            StepState.NO_VERDICT_SUBMIT_RESULT_UNKNOWN,
        ),
    ],
)
def test_step_outcomes(overrides, state):
    verdict = step_outcome(_step(**overrides))
    assert verdict.state is state
    assert verdict.is_verdict == (not state.name.startswith("NO_VERDICT"))
    assert "case='postal'" in verdict.message


def test_not_advancing_alone_is_never_a_block():
    verdict = step_outcome(_step(advanced=False, submit_status=200))
    assert verdict.state is StepState.NO_VERDICT_SUBMIT_RESULT_UNKNOWN
    assert not verdict.is_verdict


def test_unrendered_step_outranks_everything_else():
    verdict = step_outcome(_step(step_rendered=False, fields_present=0, submit_clicked=False))
    assert verdict.state is StepState.NO_VERDICT_STEP_NEVER_RENDERED


def test_rejection_message_is_a_parameter():
    verdict = step_outcome(
        _step(block_value="", advanced=False, rejection_message_shown=True),
        rejection_message="A delivery address is required.",
    )
    assert "A delivery address is required." in verdict.message


def test_expected_state_comes_from_configuration():
    assert expected_step_state(True) is StepState.FILLED_AND_ADVANCED
    assert expected_step_state(False) is StepState.EMPTY_AND_BLOCKED
    assert step_outcome(_step()).state is expected_step_state(True)


def test_join_block_values_drops_label_echoes_and_blanks():
    labels = ["Street", "Number", "Postcode", "City"]
    assert join_block_values(labels, ["Street", "Number", "Postcode", "City"]) == ""
    assert join_block_values(labels, ["Main St", " 4 ", None, "city"]) == "Main St 4"


@pytest.mark.parametrize(("labels", "values"), [(["a"], []), (None, ["x"]), (["a"], None)])
def test_join_block_values_refuses_misaligned_lists(labels, values):
    with pytest.raises(ValueError, match="line up"):
        join_block_values(labels, values)


# ---------------------------------------------------------------------------
# forbidden change
# ---------------------------------------------------------------------------


def _change(**overrides) -> RefusalObservations:
    base = dict(
        target="monthly",
        value_before="yearly",
        value_after="monthly",
        field_editable=True,
        refusal_shown=True,
        advanced=False,
    )
    base.update(overrides)
    return RefusalObservations(**base)


@pytest.mark.parametrize(
    ("overrides", "state"),
    [
        ({}, RefusalState.REFUSED),
        ({"refusal_shown": False, "advanced": True}, RefusalState.ACCEPTED_WITHOUT_NOTICE),
        ({"refusal_shown": False}, RefusalState.STOPPED_WITHOUT_MESSAGE),
        ({"advanced": True}, RefusalState.REJECTED_YET_PROCEEDED),
        ({"field_editable": False}, RefusalState.INPUT_LOCKED),
        ({"value_after": "yearly"}, RefusalState.CHANGE_IGNORED),
    ],
)
def test_refusal_outcomes(overrides, state):
    verdict = refusal_outcome(_change(**overrides))
    assert verdict.state is state
    assert verdict.passed == (state is RefusalState.REFUSED)
    assert verdict.is_verdict == (
        state not in {RefusalState.INPUT_LOCKED, RefusalState.CHANGE_IGNORED}
    )


def test_change_taken_needs_both_halves():
    already_there = _change(value_before="monthly", value_after="monthly")
    other_field = _change(value_after="30 days")
    assert not already_there.change_taken and not other_field.change_taken
    assert _change(value_after="Monthly (12x)").change_taken
    assert not _change(target="").change_taken


def test_uneditable_field_outranks_a_shown_refusal():
    verdict = refusal_outcome(_change(field_editable=False, advanced=True))
    assert verdict.state is RefusalState.INPUT_LOCKED


def test_expected_message_defaults_to_english_and_is_configurable():
    assert DEFAULT_REFUSAL_MESSAGE in refusal_outcome(_change()).message
    verdict = refusal_outcome(_change(), expected_message="Term is locked.")
    assert "Term is locked." in verdict.message
    with pytest.raises(ValueError, match="exact message"):
        RefusalClassifier(" ")


def test_classifier_is_callable_and_keeps_observations():
    observations = _change()
    verdict = RefusalClassifier()(observations)
    assert verdict.observations is observations
    assert str(verdict).startswith("REFUSED: ")
    assert replace(observations, advanced=True).advanced
