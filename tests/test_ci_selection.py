"""
The gate: no test in this repository may be invisible to CI.

``test_no_test_is_invisible_to_ci`` is the assertion that matters. Everything
else here exists to keep it trustworthy — a gate that cannot fail is worse than
no gate, because it looks like one.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from core.ci_selection import (
    PytestInvocation,
    SelectionReport,
    analyse,
    collect_node_ids,
    parse_node_ids,
    parse_pytest_invocations,
    strip_execution_only_flags,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


def test_no_test_is_invisible_to_ci():
    """
    Every collected test is selected by at least one pytest command in the
    workflow.

    A test that no CI job selects is not covered by the badge on the README. It
    passes on the author's machine and reports nothing to anybody else, and the
    build stays green whether it works or not.

    If this fails, the fix is one of two things and never a third: run the
    tests, or delete them. Widening the gate to tolerate the gap reinstates
    exactly the blind spot it was written to find.
    """
    report = analyse(WORKFLOW, ROOT)
    assert report.unrun == frozenset(), "\n" + report.describe()


def test_the_workflow_actually_runs_pytest():
    """
    Guard against the gate passing because it found nothing to check.

    An empty invocation list would make every test look uncovered, but a
    renamed workflow file or a restructured job would make ``analyse`` compare
    against nothing at all — so assert the parser really located commands.
    """
    invocations = parse_pytest_invocations(WORKFLOW)
    assert invocations, f"no pytest command found in {WORKFLOW}"


# ---------------------------------------------------------------------------
# positive control — prove the gate can fail
# ---------------------------------------------------------------------------


def _workflow(tmp_path: Path, run_body: str) -> Path:
    path = tmp_path / "synthetic.yml"
    path.write_text(
        textwrap.dedent(
            f"""\
            name: Synthetic
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - name: Run tests
                    run: {run_body}
            """
        ),
        encoding="utf-8",
    )
    return path


def test_gate_reports_a_gap_when_ci_selects_almost_nothing(tmp_path):
    """
    Positive control.

    Point the same analysis at a workflow that runs only the smoke marker — the
    state this repository was actually in — and it must report the rest of the
    suite as unrun. Without this, a bug that made ``unrun`` always empty would
    turn the gate above into a permanent, silent pass.
    """
    report = analyse(_workflow(tmp_path, "pytest -m smoke"), ROOT)

    assert len(report.all_tests) > 100, "collection looks wrong; the control proves nothing"
    assert len(report.unrun) > 100, report.describe()
    assert report.coverage_ratio < 0.1


def test_gate_is_satisfied_when_ci_runs_everything(tmp_path):
    """
    Negative control.

    A workflow with no filter at all must come back clean. Paired with the test
    above this pins both ends: the analysis distinguishes full coverage from
    near-zero coverage rather than always answering the same way.
    """
    report = analyse(_workflow(tmp_path, "pytest"), ROOT)

    assert report.unrun == frozenset(), report.describe()
    assert report.coverage_ratio == 1.0


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


class TestParseInvocations:
    def test_finds_plain_pytest(self, tmp_path):
        found = parse_pytest_invocations(_workflow(tmp_path, "pytest -m smoke -n auto"))
        assert [inv.args for inv in found] == [("-m", "smoke", "-n", "auto")]

    def test_finds_python_dash_m_pytest(self, tmp_path):
        found = parse_pytest_invocations(_workflow(tmp_path, "python -m pytest tests/"))
        assert [inv.args for inv in found] == [("tests/",)]

    def test_ignores_commands_that_are_not_pytest(self, tmp_path):
        found = parse_pytest_invocations(_workflow(tmp_path, "ruff check ."))
        assert found == []

    def test_does_not_match_pytest_inside_another_word(self, tmp_path):
        found = parse_pytest_invocations(_workflow(tmp_path, "pip install pytest-xdist"))
        assert found == []

    def test_reads_every_line_of_a_multiline_step(self, tmp_path):
        path = tmp_path / "multi.yml"
        path.write_text(
            textwrap.dedent(
                """\
                name: Multi
                on: [push]
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - name: Everything
                        run: |
                          ruff check .
                          pytest tests/
                          pytest -m smoke
                """
            ),
            encoding="utf-8",
        )
        assert [inv.args for inv in parse_pytest_invocations(path)] == [
            ("tests/",),
            ("-m", "smoke"),
        ]

    def test_skips_comments(self, tmp_path):
        path = tmp_path / "commented.yml"
        path.write_text(
            textwrap.dedent(
                """\
                name: Commented
                on: [push]
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: |
                          # pytest -m regression
                          pytest tests/
                """
            ),
            encoding="utf-8",
        )
        assert [inv.args for inv in parse_pytest_invocations(path)] == [("tests/",)]

    def test_records_job_and_step(self, tmp_path):
        found = parse_pytest_invocations(_workflow(tmp_path, "pytest"))
        assert found[0].job == "test"
        assert found[0].step == "Run tests"

    def test_unparseable_pytest_line_raises(self, tmp_path):
        path = tmp_path / "broken.yml"
        path.write_text(
            textwrap.dedent(
                """\
                name: Broken
                on: [push]
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: pytest -m "unclosed
                """
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="cannot parse a pytest command"):
            parse_pytest_invocations(path)

    def test_empty_workflow_yields_nothing(self, tmp_path):
        path = tmp_path / "empty.yml"
        path.write_text("", encoding="utf-8")
        assert parse_pytest_invocations(path) == []


class TestStripExecutionOnlyFlags:
    @pytest.mark.parametrize(
        "given,expected",
        [
            (["-m", "smoke", "-n", "auto"], ["-m", "smoke"]),
            (["tests/", "-v"], ["tests/"]),
            (["--reruns", "2", "tests/"], ["tests/"]),
            (["--numprocesses=4", "tests/"], ["tests/"]),
            (["--alluredir=reports/x", "-m", "api"], ["-m", "api"]),
        ],
    )
    def test_execution_flags_are_dropped(self, given, expected):
        assert strip_execution_only_flags(given) == expected

    def test_selection_flags_survive(self):
        args = ["-m", "smoke or api", "-k", "search", "--ignore", "ui", "tests/"]
        assert strip_execution_only_flags(args) == args

    def test_unknown_flags_are_passed_through(self):
        """
        A flag this module has not heard of must reach pytest.

        Dropping it would silently change the selection being audited, and the
        gate would under-report. Passing it through means an unknown flag that
        breaks collection fails loudly instead.
        """
        assert strip_execution_only_flags(["--some-future-flag", "tests/"]) == [
            "--some-future-flag",
            "tests/",
        ]


class TestParseNodeIds:
    def test_reads_ids_and_ignores_the_summary(self):
        output = textwrap.dedent(
            """\
            tests/test_a.py::test_one
            tests/test_b.py::TestGroup::test_two

            2 tests collected in 0.10s
            """
        )
        assert parse_node_ids(output) == {
            "tests/test_a.py::test_one",
            "tests/test_b.py::TestGroup::test_two",
        }

    def test_keeps_parametrised_ids_containing_spaces(self):
        output = "tests/test_a.py::test_one[a b]\n1 test collected\n"
        assert parse_node_ids(output) == {"tests/test_a.py::test_one[a b]"}

    def test_ignores_warnings_and_blank_lines(self):
        output = "\nwarning: something::happened elsewhere\ntests/test_a.py::test_one\n"
        assert parse_node_ids(output) == {"tests/test_a.py::test_one"}

    def test_empty_output_is_empty_set(self):
        assert parse_node_ids("") == set()


class TestCollectNodeIds:
    def test_a_marker_selects_fewer_tests_than_no_filter(self):
        everything = collect_node_ids([], ROOT)
        smoke = collect_node_ids(["-m", "smoke"], ROOT)
        assert smoke, "the smoke marker selected nothing"
        assert smoke < everything

    def test_a_marker_matching_nothing_is_an_empty_set_not_an_error(self):
        """pytest exits 5 here; that is an answer, not a failure."""
        assert collect_node_ids(["-m", "no_such_marker_anywhere"], ROOT) == set()


class TestSelectionReport:
    def test_describe_names_the_missing_tests(self, tmp_path):
        report = analyse(_workflow(tmp_path, "pytest -m smoke"), ROOT)
        described = report.describe()
        assert "selected by no CI job" in described
        assert "pytest -m smoke" in described

    def test_coverage_ratio_of_an_empty_suite_is_one(self):
        """No tests means nothing is uncovered — not a division by zero."""
        report = SelectionReport(all_tests=frozenset(), covered=frozenset(), invocations=())
        assert report.coverage_ratio == 1.0
        assert report.unrun == frozenset()

    def test_a_test_covered_twice_is_not_counted_twice(self):
        report = SelectionReport(
            all_tests=frozenset({"a::x", "b::y"}),
            covered=frozenset({"a::x"}),
            invocations=(),
        )
        assert report.coverage_ratio == 0.5
        assert report.unrun == frozenset({"b::y"})

    def test_describe_truncates_a_long_list(self):
        report = SelectionReport(
            all_tests=frozenset(f"t{i}.py::test" for i in range(50)),
            covered=frozenset(),
            invocations=(),
        )
        described = report.describe(limit=5)
        assert "... and 45 more" in described

    def test_invocation_str_is_readable(self):
        inv = PytestInvocation(job="test", step="Run", args=("-m", "smoke"))
        assert str(inv) == "test / Run: pytest -m smoke"
