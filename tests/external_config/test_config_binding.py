"""The suite's own workflow copy, checked against the external configuration checkout.

CI checks the configuration repository out into ``ext-config/`` and
``python -m gate.external_config verify`` exports ``EXT_CONFIG_PATH`` (plus the pinned
flag and commit) when it bound. Without a binding these tests skip — or fail when
``EXT_CONFIG_REQUIRED`` is true — and the verify step has already said so loudly.

The default target is this repository's own default branch (``demo_sut/config``), so the
check works with zero setup: it catches this branch's copy drifting from the configuration
the default branch ships.
"""

from __future__ import annotations

from pathlib import Path

from gate.external_config import bound_checkout, read_checks
from generators.workflow_model import load

ROOT = Path(__file__).resolve().parents[2]
SUITE_COPY = ROOT / "demo_sut" / "config" / "subscription.machine.yaml"
NAME = "subscription.machine.yaml"

CHECKS = {
    "test_external_workflow_parses": (
        "the external subscription workflow parses with the generators' workflow model"
    ),
    "test_states_match_the_suite_copy": (
        "the external workflow has the same states as the suite's own copy"
    ),
    "test_transitions_match_the_suite_copy": (
        "the external workflow has the same transitions as the suite's own copy"
    ),
}


def _external():
    path, _commit = bound_checkout((NAME,))
    return load(path / NAME)


def _transitions(definition) -> set[tuple[str, str, str]]:
    return {(state, t.action, t.to) for state, t in definition.transitions()}


def test_external_workflow_parses():
    definition = _external()
    assert definition.problems() == []


def test_states_match_the_suite_copy():
    assert set(_external().states) == set(load(SUITE_COPY).states)


def test_transitions_match_the_suite_copy():
    assert _transitions(_external()) == _transitions(load(SUITE_COPY))


def test_the_declared_check_list_matches_these_tests():
    """The verify step reads checks.txt to say what did not run; it must be this file's list."""
    assert read_checks() == list(CHECKS.values())
    assert all(callable(globals()[name]) for name in CHECKS)
