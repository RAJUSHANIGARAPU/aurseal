"""Offline unit tests for the change examples' pure decisions."""

from __future__ import annotations

import pytest

from tests.demo_examples.change.oracles import (
    DERIVED_FIELDS,
    START_CHANGE_DEFECT,
    START_CHANGE_ENVIRONMENT,
    StartingPointGap,
    blank_derived_fields,
    classify_baseline_gap,
    fields_to_compare,
    is_blank,
    rejection_text,
)
from verification.defect_detector import Attribution, attribute_failure

#: An invented holder block with one blank derived field.
HOLDER = {
    "id": 41,
    "number": "CUS-D1-41",
    "name": "Tallow Lane Studio",
    "type": "COMPANY",
    "email": "studio@example.test",
    "phone": None,
}


def test_baseline_gap_classification():
    blank = blank_derived_fields(HOLDER)
    assert blank == ["phone"], "only the blank derived field is found"
    assert classify_baseline_gap(blank, built_this_run=False) is StartingPointGap.EXCLUDE
    assert fields_to_compare(blank, StartingPointGap.EXCLUDE) == ["name", "type", "email"]


def test_empty_string_and_whitespace_count_as_blank():
    block = dict(HOLDER, phone="+1 202", email="", name="   ")
    assert blank_derived_fields(block) == ["name", "email"]


@pytest.mark.parametrize("value", [None, "", "  ", {}, [], ()])
def test_is_blank(value):
    assert is_blank(value)


@pytest.mark.parametrize("value", [0, False, "x", {"a": 1}, [0]])
def test_is_not_blank(value):
    assert not is_blank(value)


def test_a_gap_on_a_subscription_built_this_run_fails():
    assert classify_baseline_gap(["phone"], built_this_run=True) is StartingPointGap.FAIL
    assert fields_to_compare(["phone"], StartingPointGap.FAIL) == []


def test_a_borrowed_subscription_with_every_field_blank_abstains():
    blank = blank_derived_fields({})
    assert blank == list(DERIVED_FIELDS)
    assert classify_baseline_gap(blank, built_this_run=False) is StartingPointGap.ABSTAIN
    assert classify_baseline_gap(blank, built_this_run=True) is StartingPointGap.FAIL


def test_a_complete_baseline_is_fine():
    block = dict(HOLDER, phone="0201 4455")
    assert blank_derived_fields(block) == []
    for built in (True, False):
        assert classify_baseline_gap([], built_this_run=built) is StartingPointGap.NONE
    assert fields_to_compare([], StartingPointGap.NONE) == list(DERIVED_FIELDS)


def test_a_gap_that_was_blank_before_is_not_blamed_on_the_change():
    before = dict(HOLDER)
    after = dict(HOLDER)  # still blank after the change: same gap, not a new one
    gap = classify_baseline_gap(blank_derived_fields(before), built_this_run=False)
    compared = fields_to_compare(blank_derived_fields(before), gap)
    assert "phone" not in compared
    assert all(before[f] == after[f] for f in compared)


# -- rejection attribution: environment wins ------------------------------------------------


def _attribute(text: str):
    return attribute_failure(text, defect=START_CHANGE_DEFECT, environment=START_CHANGE_ENVIRONMENT)


def test_rejection_attribution_environment_wins():
    draft_exists = rejection_text(
        409, {"error": "conflict", "message": "A draft already exists for this subscription."}
    )
    no_route = rejection_text(
        404, {"error": "unknown_path", "message": "No API route for /api/drafts/9/actions/x"}
    )
    assert _attribute(draft_exists) is Attribution.ENVIRONMENT
    assert _attribute(no_route) is Attribution.ENVIRONMENT


@pytest.mark.parametrize(
    "message",
    ["Start date cannot change after the first booking.", "Subscription is not active."],
)
def test_known_validators_are_environment(message):
    text = rejection_text(422, {"error": "invalid", "message": message})
    assert _attribute(text) is Attribution.ENVIRONMENT


def test_a_rejection_naming_the_start_date_is_the_defect():
    text = rejection_text(
        422,
        {
            "error": "invalid",
            "message": "Start must be an ISO date or date-time.",
            "fields": {"start_date": "Must be an ISO date."},
        },
    )
    assert _attribute(text) is Attribution.DEFECT
    assert _attribute("422 the change scheduled outside its term year") is Attribution.DEFECT


def test_environment_wins_over_an_echoed_start_date():
    text = rejection_text(
        422,
        {
            "message": "Start date cannot change after the first booking.",
            "fields": {"start_date": "Start date cannot change after the first booking."},
        },
    )
    assert _attribute(text) is Attribution.ENVIRONMENT


@pytest.mark.parametrize(
    ("status", "body"),
    [(500, {"error": "server_error"}), (404, {"error": "not_found"}), (0, None)],
)
def test_unrecognised_rejections_are_unattributable(status, body):
    assert _attribute(rejection_text(status, body)) is Attribution.UNATTRIBUTABLE


def test_blank_is_unattributable():
    assert _attribute("") is Attribution.UNATTRIBUTABLE
    assert rejection_text(0, None) == "0"
