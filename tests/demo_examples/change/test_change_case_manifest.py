"""Self-consistency of the change-case manifest (offline: reads files only).

The manifest is the one list of plan changes with an honest state per case. These checks
keep it from drifting into claims: a case that can be driven says how, its action exists in
the lifecycle file and can end in the state it names, an automated case points at a test
that exists, and the proven count is reported apart from the automated count.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest
import yaml

from demo_sut.subscriptions import LOCKED_IN_CHANGE
from test_data.scenario_manifest import CaseManifest, CaseState

ROOT = Path(__file__).resolve().parents[3]
logger = logging.getLogger(__name__)

MANIFEST_PATH = Path(__file__).with_name("change-cases.json")
MACHINE_PATH = ROOT / "demo_sut" / "config" / "subscription.machine.yaml"
GENERIC_TEST = "tests/demo_examples/change/test_change_scenarios.py::test_change_case"
#: Case groups, by the rule that decides the change.
GROUPS = ("TERMS", "BILL", "SETUP", "OWNER", "LOCK", "LIFE")
CASE_ID = re.compile(rf"^CHG-({'|'.join(GROUPS)})-([1-9][0-9]*)$")


@pytest.fixture(scope="module")
def manifest() -> CaseManifest:
    return CaseManifest.load(MANIFEST_PATH)


@pytest.fixture(scope="module")
def machine() -> dict:
    return yaml.safe_load(MACHINE_PATH.read_text(encoding="utf-8"))


def reachable_from(machine: dict, action: str) -> set[str]:
    """States an action can leave the subscription in: its target, or one step past it."""
    targets = {
        t["to"]
        for state in machine["states"].values()
        for t in state.get("transitions") or []
        if t["action"] == action
    }
    after = {t["to"] for s in targets for t in machine["states"][s].get("transitions") or []}
    return targets | after


def functions_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def test_ids_are_unique_and_there_are_enough_cases(manifest):
    ids = [c.id for c in manifest.cases]
    assert len(ids) == len(set(ids)) and len(ids) >= 10


def test_ids_name_their_group_and_count_up_within_it(manifest):
    numbers: dict[str, list[int]] = {}
    for case in manifest.cases:
        match = CASE_ID.match(case.id)
        assert match, f"{case.id}: expected CHG-<{'|'.join(GROUPS)}>-<n>"
        numbers.setdefault(match[1], []).append(int(match[2]))
    for group, seen in numbers.items():
        assert seen == list(range(1, len(seen) + 1)), f"{group}: numbered {seen}"
    order = [GROUPS.index(CASE_ID.match(c.id)[1]) for c in manifest.cases]
    assert order == sorted(order), "cases are listed group by group"


def test_every_field_locked_for_the_term_has_a_lock_case(manifest):
    touched = {
        field
        for case in manifest.cases
        if case.id.startswith("CHG-LOCK-")
        for field in case.inputs.get("touches", [])
    }
    assert set(LOCKED_IN_CHANGE) <= touched, set(LOCKED_IN_CHANGE) - touched


def test_automated_and_planned_cases_say_how_they_are_driven(manifest):
    for case in manifest.cases:
        if case.state is CaseState.MANUAL:
            continue
        missing = [k for k in ("action", "ends_in", "reasons", "touches") if not case.inputs.get(k)]
        assert not missing, f"{case.id}: {case.state.value} but missing {missing}"


def test_actions_and_end_states_exist_in_the_lifecycle(manifest, machine):
    for case in manifest.cases:
        action, ends_in = case.inputs.get("action"), case.inputs.get("ends_in")
        assert action in machine["actions"], f"{case.id}: {action!r} is not a lifecycle action"
        assert ends_in in machine["states"], f"{case.id}: {ends_in!r} is not a lifecycle state"
        if case.is_drivable:
            assert ends_in in reachable_from(machine, action), f"{case.id}: {action} -> {ends_in}?"


def test_cases_that_cannot_run_say_why(manifest):
    for case in manifest.cases:
        if case.state is CaseState.PLANNED:
            assert case.waiting_on, f"{case.id}: planned without waiting_on"
        if case.state is CaseState.MANUAL:
            assert case.why_manual, f"{case.id}: manual without why_manual"


def test_automated_cases_point_at_a_test_that_exists(manifest):
    for case in manifest.cases:
        if case.state is not CaseState.AUTOMATED:
            continue
        assert case.test and "::" in case.test, f"{case.id}: test must be path::function"
        path, name = case.test.split("::", 1)
        assert (ROOT / path).is_file(), f"{case.id}: {path} does not exist"
        assert name in functions_in(ROOT / path), f"{case.id}: {name} is not defined in {path}"


def test_generic_cases_say_what_to_apply_and_what_to_expect(manifest):
    generic = [c for c in manifest.cases if c.test == GENERIC_TEST]
    assert generic, "no case runs through the generic test"
    for case in generic:
        assert case.inputs.get("apply") and case.inputs.get("then"), case.id


def test_every_case_records_its_origin_and_every_claim_its_proof(manifest):
    for case in manifest.cases:
        assert case.inputs.get("origin"), f"{case.id}: no origin"
        if case.seen_passing:
            assert case.proof, f"{case.id}: seen passing without proof"


def test_proven_is_reported_apart_from_automated(manifest):
    summary = manifest.summary()
    automated = summary.get(CaseState.AUTOMATED.value, 0)
    assert "proven" in summary and summary["proven"] <= automated
    proven = {c.id for c in manifest.cases if c.is_proven}
    assert proven <= {c.id for c in manifest.cases if c.state is CaseState.AUTOMATED}
    logger.info(
        "change cases: %s cases, automated %s, planned %s, manual %s, proven %s",
        len(manifest.cases),
        automated,
        summary.get(CaseState.PLANNED.value, 0),
        summary.get(CaseState.MANUAL.value, 0),
        summary["proven"],
    )
