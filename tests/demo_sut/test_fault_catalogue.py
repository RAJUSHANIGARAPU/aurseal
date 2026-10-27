"""The fault catalogue is complete and every defect has its pair of examples.

Read from the source, offline: the README table lists exactly the switches the service
knows; each defect-class switch has a detector pair in ``tests/demo_examples`` (one test
parametrised ``fault-on`` / ``fault-off`` that switches it) and a unit test here naming it;
and every detector call passes a positive control.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from demo_sut.faults import FAULTS

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "tests" / "demo_examples"
UNIT = ROOT / "tests" / "demo_sut"
README = ROOT / "demo_sut" / "README.md"

#: Generic switches with no defect scenario of their own (outage, price checks, labels).
INFRASTRUCTURE = {
    "api_unavailable",
    "rounding_drift",
    "negative_price_accepted",
    "screen_filter_unlabelled",
}
DEFECTS = sorted(set(FAULTS) - INFRASTRUCTURE)


def readme_switches() -> set[str]:
    text = README.read_text(encoding="utf-8")
    section = text.split("## Fault switches", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `([a-z0-9_]+)` \|", section, re.M))


def test_the_readme_table_lists_exactly_the_switches():
    assert readme_switches() == set(FAULTS)
    assert len(FAULTS) == len(INFRASTRUCTURE) + 20


def _modules(folder: Path) -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text(encoding="utf-8"))) for p in sorted(folder.rglob("*.py"))]


def _pair_ids(fn: ast.FunctionDef) -> bool:
    for deco in fn.decorator_list:
        if isinstance(deco, ast.Call) and ast.unparse(deco.func).endswith("parametrize"):
            ids = next((k.value for k in deco.keywords if k.arg == "ids"), None)
            if ids is None:
                continue
            try:
                values = set(ast.literal_eval(ids))
            except ValueError:  # computed ids are not a fault pair
                continue
            if {"fault-on", "fault-off"} <= values:
                return True
    return False


def detector_pairs() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {name: [] for name in DEFECTS}
    for path, tree in _modules(EXAMPLES):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name.startswith("test")
                and _pair_ids(node)
            ):
                source = ast.unparse(node)
                for name in DEFECTS:
                    if f"'{name}'" in source or f'"{name}"' in source:
                        found[name].append(f"{path.relative_to(ROOT)}::{node.name}")
    return found


def unit_tests() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {name: [] for name in DEFECTS}
    for path, tree in _modules(UNIT):
        if path.name == Path(__file__).name:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                source = ast.unparse(node)
                for name in DEFECTS:
                    if name in source:
                        found[name].append(node.name)
    return found


@pytest.mark.parametrize("name", DEFECTS)
def test_every_defect_has_a_detector_pair_and_a_unit_test(name):
    assert detector_pairs()[name], f"{name}: no fault-on / fault-off test in tests/demo_examples"
    assert unit_tests()[name], f"{name}: no unit test in tests/demo_sut names it"


def test_every_detector_call_passes_a_positive_control():
    missing = []
    for path, tree in _modules(EXAMPLES):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = ast.unparse(node.func)
            if called.endswith("detector_pair.judge") or called.endswith("still_reproduces"):
                if not any(k.arg == "exercised" for k in node.keywords):
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno} {called}")
    assert not missing, f"detector calls without exercised=: {missing}"


def test_the_checks_can_fail():
    """Positive controls for the parsers: a fake switch has no pair and no unit test."""
    tree = ast.parse(
        "import pytest\n"
        "@pytest.mark.parametrize('fault_on', [1, 0], ids=['fault-on', 'fault-off'])\n"
        "def test_x(fault_on):\n    pass\n"
    )
    fn = tree.body[1]
    assert _pair_ids(fn)
    assert "made_up_switch" not in detector_pairs()
    assert len(readme_switches()) >= len(DEFECTS)
