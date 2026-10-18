"""``--plan`` deselects everything outside the plan; a plan matching nothing exits 5."""

from __future__ import annotations

from tests.traceability.conftest import REGISTRY
from tests.traceability.test_plan_resolve import PLAN, TREE

pytest_plugins = ["pytester"]
ARGS = (
    "-p",
    "traceability.plan_plugin",
    "-p",
    "no:cacheprovider",
    "-W",
    "ignore::pytest.PytestAssertRewriteWarning",
)


def _setup(pytester, text):
    labelled = REGISTRY.replace("    title: first\n", "    title: first\n    labels: [billing]\n")
    pytester.makefile(".yaml", **{"requirements/requirements": labelled})
    pytester.makepyfile(test_tree=TREE)
    (pytester.path / "plans").mkdir()
    (pytester.path / "plans" / "p1.yaml").write_text(text)


def test_plan_deselects_outside_scope(pytester, subprocess_env):
    _setup(pytester, PLAN)
    result = pytester.runpytest_subprocess(*ARGS, "--plan", "plans/p1.yaml")
    result.assert_outcomes(passed=2, deselected=2)
    # xdist workers apply the same selection (the controller does not count deselections)
    pytester.runpytest_subprocess(*ARGS, "--plan", "plans/p1.yaml", "-n", "2").assert_outcomes(
        passed=2
    )


def test_a_plan_matching_nothing_exits_5(pytester, subprocess_env):
    _setup(
        pytester,
        PLAN.replace("- marker: smoke\n", "- marker: nothing_has_this\n")
        .replace("    - requirement_labels: [billing]\n", "")
        .replace("    - requirements: [REQ-2]\n", "")
        .replace("  exclude:\n    - marker: slow\n", ""),
    )
    result = pytester.runpytest_subprocess(*ARGS, "--plan", "plans/p1.yaml")
    assert result.ret == 5
    result.stdout.fnmatch_lines(["*matched nothing*"])


def test_an_invalid_plan_is_a_usage_error(pytester, subprocess_env):
    _setup(pytester, "id: p1\n")
    result = pytester.runpytest_subprocess(*ARGS, "--plan", "plans/p1.yaml")
    assert result.ret == 4
    result.stderr.fnmatch_lines(["*--plan:*"])
