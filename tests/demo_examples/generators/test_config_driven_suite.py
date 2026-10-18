"""Config-driven cases generated from the demo's machine file, run against the demo.

Definition-only cases (key, consistency plus the served machine, declared actions) run
offline; the render cases open the demo's screens in a browser and carry ``demo_ui``.
A render case FAILS when ``[data-action]`` is missing; nothing is only logged.
"""

from __future__ import annotations

import pytest

from generators.config_suite import (
    ADAPTER_KINDS,
    attach_graph,
    generate_cases,
    run_case,
    with_marks,
)
from generators.workflow_model import load
from tests.demo_examples.generators._demo_machine import MACHINE_FILE

DEFINITION = load(MACHINE_FILE)
CASES = with_marks(generate_cases(DEFINITION), ADAPTER_KINDS, [pytest.mark.demo_ui])


@pytest.mark.parametrize("case", CASES)
def test_config_driven_case(case, demo_machine):
    attach_graph(DEFINITION)
    run_case(case, demo_machine)


def test_the_served_machine_is_the_file_the_cases_came_from(demo_machine):
    served = demo_machine.fetch_machine("subscription")
    assert served["key"] == DEFINITION.key
    assert sorted(served["states"]) == sorted(DEFINITION.states)
    assert demo_machine.fetch_machine("no-such-machine") is None
