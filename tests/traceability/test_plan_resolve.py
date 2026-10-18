"""Selectors resolve to an exact test list; a selector that matches nothing is an error."""

from __future__ import annotations

from tests.traceability.conftest import REGISTRY
from traceability.cli import main as trace_main
from traceability.plans import lint_plans, load_plan, nodeid_matches, resolve_plan

pytest_plugins = ["pytester"]

TREE = """
import pytest

@pytest.mark.smoke
def test_smoke():
    pass

@pytest.mark.requirement("REQ-1")
def test_req1():
    pass

@pytest.mark.requirement("REQ-2")
@pytest.mark.slow
def test_req2_slow():
    pass

def test_plain():
    pass
"""


def _setup(pytester, plan_text, name="p1"):
    pytester.makefile(
        ".yaml",
        **{
            "requirements/requirements": REGISTRY.replace(
                "    title: first\n", "    title: first\n    labels: [billing]\n"
            )
        },
    )
    pytester.makepyfile(test_tree=TREE)
    path = pytester.path / "plans" / f"{name}.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(plan_text)
    return path


PLAN = """\
id: p1
scope:
  include:
    - marker: smoke
    - requirement_labels: [billing]
    - requirements: [REQ-2]
  exclude:
    - marker: slow
exit_criteria:
  run_integrity: PASS
"""


def test_selectors_resolve_to_an_exact_list(pytester, subprocess_env):
    path = _setup(pytester, PLAN)
    sel = resolve_plan(load_plan(path), pytester.path)
    assert sel.nodeids == ["test_tree.py::test_smoke", "test_tree.py::test_req1"]
    assert sel.ok and sel.requirements == ["REQ-1", "REQ-2"]


def test_an_unmatched_selector_is_an_error(pytester, subprocess_env, capsys):
    path = _setup(pytester, PLAN.replace("marker: smoke", "marker: nonexistent"))
    sel = resolve_plan(load_plan(path), pytester.path)
    assert sel.unmatched_selectors == ["include marker: nonexistent"] and not sel.ok
    rc = trace_main(["plan", "resolve", str(path), "--rootdir", str(pytester.path)])
    assert rc == 1 and "nonexistent" in capsys.readouterr().out


def test_lint_flags_unmatched_selectors_and_inactive_requirements(pytester, subprocess_env):
    _setup(pytester, PLAN.replace("[REQ-2]", "[REQ-9]"))
    problems = lint_plans(pytester.path / "plans", pytester.path)
    assert any("selector matches nothing: include requirements: REQ-9" in p for p in problems)
    assert any("requirement REQ-9 is not active" in p for p in problems)


def test_lint_passes_a_good_plan(pytester, subprocess_env):
    _setup(pytester, PLAN)
    assert lint_plans(pytester.path / "plans", pytester.path) == []
    assert (
        trace_main(["plan", "lint", str(pytester.path / "plans"), "--rootdir", str(pytester.path)])
        == 0
    )


def test_nodeid_patterns():
    assert nodeid_matches("tests/a/", "tests/a/test_x.py::t")
    assert nodeid_matches("tests/a", "tests/a/test_x.py::t")
    assert not nodeid_matches("tests/a", "tests/ab/test_x.py::t")
    assert nodeid_matches("tests/a/test_x.py", "tests/a/test_x.py::t")
    assert nodeid_matches("tests/*/test_x.py::*", "tests/a/test_x.py::t")
