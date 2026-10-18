"""The zero-code factory over the demo: register the machine file, get the whole suite.

Adding an entity means adding its file to ``WORKFLOW_FILES``; the hook below expands every
file into render, reachability and traversal-path cases. Screen cases carry ``demo_ui``.
"""

from __future__ import annotations

import pytest

from generators.config_suite import run_case
from generators.yaml_suite import pytest_generate_tests  # noqa: F401 - the generating hook
from tests.demo_examples.generators._demo_machine import MACHINE_FILE, feasible

WORKFLOW_FILES = [MACHINE_FILE]
WORKFLOW_KNOWN_DEFECTS = {"subscription": {"suspended": "BUG-101"}}
WORKFLOW_FEASIBLE = {"subscription": feasible}
WORKFLOW_ADAPTER_MARKS = [pytest.mark.demo_ui]


def test_generated_cases_pass_against_demo(yaml_case, demo_machine):
    run_case(yaml_case, demo_machine)
