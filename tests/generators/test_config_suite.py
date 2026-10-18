"""Config-driven cases: what is generated from a definition, and that each case can fail."""

from __future__ import annotations

from pathlib import Path

import pytest

from generators.config_suite import Case, attach_graph, generate_cases, run_case
from generators.workflow_model import from_mapping, load
from tests.generators._fake_machine import FakeMachine

MACHINE = Path(__file__).resolve().parents[2] / "demo_sut" / "config" / "subscription.machine.yaml"
DEMO = load(MACHINE)


def cases(definition=DEMO, **kw):
    return [p.values[0] for p in generate_cases(definition, **kw)]


def ids(definition=DEMO, **kw):
    return [p.id for p in generate_cases(definition, **kw)]


def test_generated_case_ids_cover_config_reachability_actions_and_renders():
    got = ids()
    assert got[:2] == ["subscription:config_key", "subscription:initial_reachable"]
    assert "subscription:declared_actions:changing" in got
    assert "subscription:declared_actions:cancelled" not in got  # terminal
    assert "subscription:action_renders:draft:activate" in got
    assert "subscription:action_renders:changing:confirm_renew" in got
    # an unreachable state has no screen to open: no render case for it
    assert not any(i.startswith("subscription:action_renders:suspended") for i in got)
    renders = [i for i in got if ":action_renders:" in i]
    assert len(renders) == 2 + 1 + 4 + 4 + 4  # draft, pending_payment, active, changing, renewed


def test_every_generated_case_passes_against_a_faithful_system():
    sut = FakeMachine(DEMO)
    for case in cases():
        run_case(case, sut)


def test_a_missing_action_fails_the_render_case_instead_of_warning():
    case = Case("action_renders", DEMO, state="active", action="renew")
    with pytest.raises(AssertionError, match=r"action 'renew' is not rendered in state 'active'"):
        run_case(case, FakeMachine(DEMO, hidden={"renew"}))


def test_a_known_missing_action_becomes_a_strict_xfail():
    params = generate_cases(DEMO, known_missing={("active", "renew"): "BUG-7 renew button"})
    (marked,) = [p for p in params if p.id == "subscription:action_renders:active:renew"]
    (mark,) = marked.marks
    assert mark.name == "xfail" and mark.kwargs["strict"] is True
    assert mark.kwargs["reason"] == "BUG-7 renew button"


def test_known_missing_for_an_unknown_pair_is_rejected():
    with pytest.raises(ValueError, match="known_missing"):
        generate_cases(DEMO, known_missing={("suspended", "resume"): "x"})


def test_initial_reachable_checks_self_consistency_and_the_served_machine():
    case = Case("initial_reachable", DEMO)
    run_case(case, FakeMachine(DEMO))
    with pytest.raises(AssertionError, match="served initial states"):
        run_case(case, FakeMachine(DEMO, served={"key": "subscription", "initial_states": ["x"]}))
    with pytest.raises(pytest.skip.Exception, match="does not serve machine"):
        run_case(case, FakeMachine(DEMO, served=None))
    broken = from_mapping({"key": "b", "initial_states": ["ghost"], "states": {"a": {}}})
    with pytest.raises(AssertionError, match="does not hang together"):
        run_case(Case("initial_reachable", broken))


def test_declared_actions_fails_for_an_undeclared_action():
    d = from_mapping(
        {
            "key": "d",
            "initial_states": ["a"],
            "states": {"a": {"transitions": [{"action": "x", "to": "a"}]}},
        }
    )
    with pytest.raises(AssertionError, match=r"undeclared actions \['x'\]"):
        run_case(Case("declared_actions", d, state="a"))


def test_config_key_fails_when_blank():
    d = from_mapping({"key": " ", "initial_states": ["a"], "states": {"a": {}}})
    with pytest.raises(AssertionError, match="key is blank"):
        run_case(Case("config_key", d))


def test_adapter_cases_without_an_adapter_say_so():
    with pytest.raises(ValueError, match="needs a MachineAdapter"):
        run_case(Case("action_renders", DEMO, state="active", action="note"))


def test_the_ascii_graph_can_be_attached():
    assert attach_graph(DEMO).endswith("unreachable: suspended")
