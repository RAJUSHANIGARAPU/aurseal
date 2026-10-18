"""Graph analysis: path enumeration, cycles, depth cap, unreachable states, ASCII."""

from __future__ import annotations

from pathlib import Path

from generators import state_graph
from generators.state_graph import StateGraph
from generators.workflow_model import from_mapping, load

MACHINE = Path(__file__).resolve().parents[2] / "demo_sut" / "config" / "subscription.machine.yaml"


def machine(states, initial=("a",)):
    return from_mapping(
        {
            "key": "m",
            "initial_states": list(initial),
            "states": {
                name: {"transitions": [{"action": f"{name}_{to}", "to": to} for to in targets]}
                for name, targets in states.items()
            },
            "actions": {f"{name}_{to}": {} for name, targets in states.items() for to in targets},
        }
    )


def test_the_demo_machine_has_exactly_one_unreachable_state():
    d = load(MACHINE)
    assert state_graph.unreachable(d) == ["suspended"]
    assert "suspended" not in StateGraph(d).reachable()


def test_every_transition_of_a_reachable_state_is_walked_by_some_path():
    graph = StateGraph(load(MACHINE))
    found = graph.paths()
    assert graph.uncovered(found) == []
    assert all(p.entry_state == "draft" for p in found)
    assert sum(p.happy for p in found) == 1 and found[0].happy


def test_paths_end_at_terminal_states_and_on_the_edge_that_closes_a_cycle():
    d = machine({"a": ["b"], "b": ["a", "c"], "c": []})
    found = state_graph.paths(d)
    assert [p.name for p in found] == ["a->b->a", "a->b->c"]
    assert found[0].closes_cycle and not found[1].closes_cycle
    assert found[1].terminal_state == "c" and found[1].actions == ("a_b", "b_c")
    assert found[1].describe() == "a --a_b--> b --b_c--> c"


def test_a_self_loop_is_a_one_step_cycle():
    found = state_graph.paths(machine({"a": ["a"]}))
    assert [p.name for p in found] == ["a->a"] and found[0].closes_cycle


def test_the_depth_cap_truncates_long_chains():
    chain = {str(i): [str(i + 1)] for i in range(30)}
    chain["30"] = []
    found = StateGraph(machine(chain, initial=("0",))).paths(max_depth=5)
    assert len(found) == 1 and found[0].truncated and len(found[0].steps) == 5


def test_conditions_are_carried_on_the_path():
    d = load(MACHINE)
    activate = next(p for p in state_graph.paths(d) if p.actions[0] == "activate")
    assert activate.conditions[0] == "activate_immediately == true"


def test_transitions_to_undeclared_states_are_left_out_of_the_graph():
    d = from_mapping(
        {
            "key": "m",
            "initial_states": ["a"],
            "states": {"a": {"transitions": [{"action": "x", "to": "ghost"}]}},
        }
    )
    assert StateGraph(d).adjacency == {"a": []}
    assert [p.name for p in state_graph.paths(d)] == ["a"]


def test_ascii_lists_transitions_conditions_terminals_and_unreachable():
    text = state_graph.ascii(load(MACHINE))
    assert text.splitlines()[0] == "subscription (initial: draft)"
    assert "--activate--> [active]  if activate_immediately == true" in text
    assert "[cancelled]" in text and "(terminal)" in text
    assert text.splitlines()[-1] == "unreachable: suspended"
