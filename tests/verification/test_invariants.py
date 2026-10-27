"""Tests for verification.invariants."""

from __future__ import annotations

import copy

import pytest

from verification.invariants import (
    InvariantSet,
    advanced_by,
    assert_invariants,
    changed,
    check_invariants,
    custom,
    no_item_in_state,
    non_empty,
    scaled_by,
    unchanged,
)

BEFORE = {
    "schedule": {"next_run": "2026-01-31", "owner": "team-a"},
    "limits": {"max": 200.0},
    "history": [],
    "tasks": [{"status": "PENDING"}, {"status": "PENDING"}],
}


def _correct_after():
    after = copy.deepcopy(BEFORE)
    after["schedule"]["next_run"] = "2026-02-28"
    after["limits"]["max"] = 204.0
    after["history"].append({"event": "advanced"})
    after["tasks"] = [{"status": "DONE"}, {"status": "DONE"}]
    return after


RULES = InvariantSet(
    advanced_by("schedule.next_run", months=1),
    scaled_by("limits.max", 1.02, rel_tol=1e-9),
    unchanged("schedule.owner"),
    non_empty("history"),
    no_item_in_state("tasks", "status", "PENDING", "RETRYING"),
)


def test_correct_transition_satisfies_every_invariant():
    assert check_invariants(BEFORE, _correct_after(), RULES) == []


def test_period_advanced_wrongly_is_caught():
    after = _correct_after()
    after["schedule"]["next_run"] = "2026-03-03"  # +31 days, not +1 month
    (violation,) = check_invariants(BEFORE, after, RULES)
    assert "expected 2026-02-28" in violation.reason


def test_not_advanced_at_all_is_caught():
    after = _correct_after()
    after["schedule"]["next_run"] = BEFORE["schedule"]["next_run"]
    assert len(check_invariants(BEFORE, after, RULES)) == 1


def test_scaling_outside_tolerance_is_caught():
    after = _correct_after()
    after["limits"]["max"] = 204.1
    (violation,) = check_invariants(BEFORE, after, RULES)
    assert "204.1" in violation.reason


def test_scaling_within_tolerance_passes():
    after = _correct_after()
    after["limits"]["max"] = 204.004
    rules = InvariantSet(scaled_by("limits.max", 1.02, abs_tol=0.01))
    assert check_invariants(BEFORE, after, rules) == []


def test_leftover_pending_items_are_named():
    after = _correct_after()
    after["tasks"][1]["status"] = "RETRYING"
    (violation,) = check_invariants(BEFORE, after, RULES)
    assert "[1]='RETRYING'" in violation.reason


def test_every_violation_is_reported_not_just_the_first():
    with pytest.raises(AssertionError) as excinfo:
        assert_invariants(BEFORE, copy.deepcopy(BEFORE), RULES)
    message = str(excinfo.value)
    assert message.startswith("4 invariant(s) violated")
    assert "history non-empty: is empty" in message


def test_missing_path_is_a_violation_not_a_crash():
    after = _correct_after()
    del after["limits"]
    (violation,) = check_invariants(BEFORE, after, RULES)
    assert "path not found" in violation.reason


def test_sets_compose_and_custom_rules_plug_in():
    extra = custom("history grew by one", lambda b, a: None if len(a["history"]) == 1 else "no")
    combined = RULES + InvariantSet(changed("limits.max"), extra)
    assert len(combined) == len(RULES) + 2
    assert check_invariants(BEFORE, _correct_after(), combined) == []
    assert len(check_invariants(BEFORE, BEFORE, InvariantSet(changed("limits.max")))) == 1


def test_no_item_in_state_requires_a_state():
    with pytest.raises(ValueError):
        no_item_in_state("tasks", "status")


# --- known wrong readings ---------------------------------------------------------


def test_a_miss_equal_to_the_old_value_says_unchanged():
    rule = scaled_by("price", 1.02, rel_tol=1e-9)
    (violation,) = InvariantSet(rule).check({"price": 100.0}, {"price": 100.0})
    assert violation.reason.endswith("got 100.0 (unchanged)")


def test_a_miss_names_every_matching_wrong_model():
    rule = scaled_by(
        "price",
        1.02,
        abs_tol=1e-9,
        readings={"bare factor": lambda old: old * 0.02, "factor only": lambda _old: 0.02 * 100},
    )
    (violation,) = InvariantSet(rule).check({"price": 100.0}, {"price": 2.0})
    assert "got 2.0 (bare factor, factor only)" in violation.reason


def test_a_miss_matching_no_known_model_names_none():
    (violation,) = InvariantSet(scaled_by("price", 1.02)).check({"price": 100.0}, {"price": 7.0})
    assert violation.reason == "expected 102.0 (100.0 x 1.02), got 7.0"


def test_a_date_that_did_not_move_says_unchanged_and_other_readings_apply():
    from datetime import date, timedelta

    rule = advanced_by(
        "term_end", months=1, readings={"advanced by days": lambda old: old + timedelta(days=30)}
    )
    (same,) = InvariantSet(rule).check({"term_end": "2026-01-31"}, {"term_end": "2026-01-31"})
    assert "got 2026-01-31 (unchanged)" in same.reason
    (days,) = InvariantSet(rule).check({"term_end": "2026-01-31"}, {"term_end": "2026-03-02"})
    assert "(advanced by days)" in days.reason
    moved = InvariantSet(rule).check({"term_end": date(2026, 1, 31)}, {"term_end": "2026-02-28"})
    assert moved == []
