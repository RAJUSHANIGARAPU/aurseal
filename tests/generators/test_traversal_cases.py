"""Traversal-path cases and unreachable-state cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from generators.config_suite import (
    UNREACHABLE_SIGNATURE,
    Case,
    covering,
    path_cases,
    run_case,
    unreachable_cases,
)
from generators.state_graph import StateGraph
from generators.workflow_model import load
from tests.generators._fake_machine import FakeMachine

DEMO = load(
    Path(__file__).resolve().parents[2] / "demo_sut" / "config" / "subscription.machine.yaml"
)


def test_path_cases_are_named_by_their_state_chain_and_actions():
    params = path_cases(DEMO)
    assert params[0].id == (
        "subscription:path:draft-submit->pending_payment-confirm->active"
        "-change->changing-confirm_change->active"
    )
    assert len({p.id for p in params}) == len(params)
    assert len(params) == len(StateGraph(DEMO).paths())


def test_a_covering_set_still_walks_every_transition():
    graph = StateGraph(DEMO)
    chosen = [p.values[0].path for p in path_cases(DEMO, cover_only=True)]
    assert len(chosen) < len(graph.paths())
    assert graph.uncovered(chosen) == []
    assert covering([]) == []


def test_feasible_drops_paths_the_system_cannot_walk():
    def no_activate(path):
        return "activate" not in path.actions

    kept = [p.values[0].path for p in path_cases(DEMO, feasible=no_activate)]
    assert kept and all("activate" not in p.actions for p in kept)


def test_walking_a_path_checks_every_target_state():
    case = path_cases(DEMO)[1].values[0]
    sut = FakeMachine(DEMO)
    run_case(case, sut)
    assert sut.log[0] == "start draft" and sut.log[-1].endswith("-> cancelled")


def test_a_transition_landing_in_the_wrong_state_fails_naming_the_step():
    case = path_cases(DEMO)[0].values[0]
    with pytest.raises(
        AssertionError, match=r'after pending_payment --confirm--> .*\[data-state="active"\]'
    ):
        run_case(case, FakeMachine(DEMO, wrong_target={"confirm": "draft"}))


def test_unreachable_state_is_a_real_failing_case():
    case = Case("state_reachable", DEMO, state="suspended")
    with pytest.raises(AssertionError, match=UNREACHABLE_SIGNATURE):
        run_case(case)
    run_case(Case("state_reachable", DEMO, state="renewed"))


def test_a_known_unreachable_state_carries_a_known_defect_marker():
    params = unreachable_cases(DEMO, {"suspended": "BUG-9"})
    assert len(params) == len(DEMO.states)
    (marked,) = [p for p in params if p.marks]
    assert marked.id == "subscription:state_reachable:suspended"
    (mark,) = marked.marks
    assert mark.name == "known_defect" and mark.args == ("BUG-9",)
    assert mark.kwargs["match"] == UNREACHABLE_SIGNATURE


def test_a_known_defect_for_an_undeclared_state_is_rejected():
    with pytest.raises(ValueError, match="undeclared states"):
        unreachable_cases(DEMO, {"paused": "BUG-9"})
