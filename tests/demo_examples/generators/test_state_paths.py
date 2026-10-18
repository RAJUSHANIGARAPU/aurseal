"""Traversal paths of the demo's machine, walked in the demo, plus the unreachable-state case.

Each path case drives one transition at a time and checks the screen shows
``[data-state="<target>"]`` after it. The paths are a covering set (every transition the
demo can walk is walked once at least); paths the demo cannot walk are dropped by
``feasible`` (a draft is confirmed by its own kind's confirm).

``suspended`` has no incoming transition. That is a real, failing reachability case,
reported through ``known_defect`` instead of a soft warning: it stays green while the
state is unreachable and goes red the day something leads into it.
"""

from __future__ import annotations

import pytest

from generators.config_suite import (
    ADAPTER_KINDS,
    attach_graph,
    path_cases,
    run_case,
    unreachable_cases,
    with_marks,
)
from generators.state_graph import StateGraph
from generators.workflow_model import load
from tests.demo_examples.generators._demo_machine import MACHINE_FILE, feasible

DEFINITION = load(MACHINE_FILE)
PATHS = with_marks(
    path_cases(DEFINITION, feasible=feasible, cover_only=True), ADAPTER_KINDS, [pytest.mark.demo_ui]
)


@pytest.mark.parametrize("case", PATHS)
def test_state_path(case, demo_machine):
    attach_graph(DEFINITION)
    run_case(case, demo_machine)


@pytest.mark.parametrize("case", unreachable_cases(DEFINITION, {"suspended": "BUG-101"}))
def test_state_reachable(case):
    run_case(case)


def test_the_paths_walk_every_transition_the_demo_can_walk():
    graph = StateGraph(DEFINITION)
    walked = [p.values[0].path for p in PATHS]
    feasible_all = [p for p in graph.paths() if feasible(p)]
    assert graph.uncovered(walked) == graph.uncovered(feasible_all)
    assert graph.uncovered(walked) == []  # (out of reachable states)
