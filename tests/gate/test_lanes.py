"""Execution policy: gate exclusions, opt-in lanes, unit first, sequential state-sharing tests."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from core.ci_selection import parse_pytest_invocations
from gate.lanes import (
    LANE_ORDER,
    LANES,
    BringupError,
    bringup_command,
    bringup_env,
    bringup_node_id,
    excluded_markers,
    gate_command,
    gate_marker_expression,
    lane,
    main,
    needs_sequential,
    resolve_selection,
    run_bringup,
    unit_lane_command,
    xdist_args,
)
from gate.workflow_lint import lint_workflow

ROOT = Path(__file__).resolve().parents[2]


def test_default_gate_excludes_unit_and_every_opt_in_lane():
    assert excluded_markers({}) == ["unit", "on_demand", "slow", "draft", "render_check"]
    assert gate_marker_expression(env={}) == (
        "not unit and not on_demand and not slow and not draft and not render_check"
    )


def test_opt_in_toggle_brings_a_lane_back():
    env = {"INCLUDE_MANUAL_CHECKS": "true"}
    assert excluded_markers(env) == ["unit", "slow", "draft", "render_check"]
    assert gate_marker_expression("smoke or regression", env) == (
        "(smoke or regression) and not unit and not slow and not draft and not render_check"
    )


def test_unit_is_excluded_even_when_asked_for_by_toggles():
    assert "unit" in excluded_markers({"RUN_UNIT_TESTS": "true"})


def test_state_sharing_tests_run_sequentially_unless_declared_isolated():
    assert needs_sequential(["e2e"], {}) and needs_sequential(["smoke", "stateful"], {})
    assert not needs_sequential(["smoke", "unknown"], {})
    assert xdist_args(["e2e"], {}) == ["-n", "0"]
    assert xdist_args(["e2e"], {"E2E_PARALLEL_SAFE": "yes"}) == ["-n", "auto"]


def test_gate_command_and_lane_order():
    assert LANE_ORDER[0] == "unit"
    assert unit_lane_command() == ["pytest", "-m", "unit", "-n", "auto"]
    assert gate_command(env={}) == [
        "pytest",
        "-m",
        "not unit and not on_demand and not slow and not draft and not render_check",
        "-n",
        "0",
        "--reruns",
        "1",
    ]


def test_lane_lookup():
    assert lane("unit").excluded_from_gate
    with pytest.raises(KeyError):
        lane("nope")
    assert len({candidate.marker for candidate in LANES}) == len(LANES)


# ---------------------------------------------------------------------------
# bring-up branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "node_id"),
    [
        ("bringup/tests/gate/test_lanes.py", "tests/gate/test_lanes.py"),
        ("refs/heads/bringup/tests/a/test_b.py/TestX/test_y", "tests/a/test_b.py::TestX::test_y"),
        ("bringup/tests/gate/", "tests/gate"),
        ("bringup/test_lanes", "test_lanes"),
        ("bringup/tests/a/test_b.py/test_case[one]", "tests/a/test_b.py::test_case[one]"),
    ],
)
def test_branch_name_maps_to_a_node_id(ref, node_id):
    assert bringup_node_id(ref) == node_id


@pytest.mark.parametrize(
    ("ref", "problem"),
    [
        ("feature/x", "not a bring-up branch"),
        ("bringup/", "empty bring-up suffix"),
        ("bringup///", "empty bring-up suffix"),
        ("bringup/tests//test_a.py", "empty or relative"),
        ("bringup/../secrets.py", "empty or relative"),
    ],
)
def test_bad_branch_names_are_errors_not_everything(ref, problem):
    with pytest.raises(BringupError, match=problem):
        bringup_node_id(ref)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_orders.py").write_text(
        "def test_total():\n    assert 1\n\ndef test_tax():\n    assert 1\n"
    )
    (tmp_path / "tests" / "unit" / "test_refunds.py").write_text("def test_r():\n    assert 1\n")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "test_orders.py").write_text("")
    return tmp_path


def test_resolve_paths_modules_globs_and_prefixes(tree):
    assert resolve_selection("tests/unit/test_orders.py::test_total", tree) == [
        "tests/unit/test_orders.py::test_total"
    ]
    assert resolve_selection("test_orders", tree) == ["tests/unit/test_orders.py"]
    assert resolve_selection("tests/unit/test_*.py", tree) == [
        "tests/unit/test_orders.py",
        "tests/unit/test_refunds.py",
    ]
    assert resolve_selection("tests/unit/test_orders.py::test_t*", tree) == [
        "tests/unit/test_orders.py",
        "-k",
        "test_t",
    ]
    assert resolve_selection("tests/unit", tree) == ["tests/unit"]


@pytest.mark.parametrize(
    ("node_id", "problem"),
    [
        ("tests/unit/test_missing.py", "does not exist"),
        ("test_nothing", "exactly one file, found: none"),
        ("tests/none_*.py", "matches no file"),
        ("tests/unit/test_*.py::test_x", "glob path cannot carry"),
        ("tests/unit/test_orders.py::*", "bare"),
        ("/etc/passwd", "inside the repository"),
        ("../x.py", "inside the repository"),
    ],
)
def test_resolve_refuses_what_names_nothing(tree, node_id, problem):
    with pytest.raises(BringupError, match=problem):
        resolve_selection(node_id, tree)


def test_ambiguous_module_name_is_refused(tree):
    (tree / "tests" / "test_orders.py").write_text("")
    with pytest.raises(BringupError, match="exactly one file"):
        resolve_selection("test_orders", tree)


def test_bringup_command_drops_exclusions_and_reruns(tree):
    command = bringup_command("bringup/tests/unit/test_orders.py", tree, env={})
    assert command == ["pytest", "tests/unit/test_orders.py", "-m", "", "-n", "0"]
    assert "--reruns" not in command


def test_selection_override_wins_and_takes_several(tree):
    env = {"BRINGUP_SELECTION": "tests/unit/test_orders.py::test_tax test_refunds"}
    assert bringup_command("main", tree, env=env)[1:3] == [
        "tests/unit/test_orders.py::test_tax",
        "tests/unit/test_refunds.py",
    ]


def test_bringup_env_turns_every_lane_on_and_requires_a_verdict():
    env = bringup_env({"PATH": "/bin"})
    assert env["INCLUDE_MANUAL_CHECKS"] == env["RUN_SLOW_TESTS"] == "true"
    assert env["COVERAGE_VERDICT_REQUIRED"] == "true" and env["PATH"] == "/bin"


@pytest.mark.parametrize("code", [0, 1, 4, 5])
def test_pytest_exit_status_is_returned_unchanged(tree, code):
    seen = {}

    def fake_runner(command, cwd, env, check):
        seen.update(command=command, cwd=cwd, env=env)
        return subprocess.CompletedProcess(command, code)

    assert run_bringup("bringup/test_orders", tree, env={}, runner=fake_runner) == code
    assert seen["command"][:3] == [sys.executable, "-m", "pytest"]
    assert seen["env"]["COVERAGE_VERDICT_REQUIRED"] == "true"


def test_selected_nothing_exits_five_for_real(tree, monkeypatch):
    monkeypatch.setenv("BRINGUP_SELECTION", "tests/unit/test_orders.py::no_such_name*")
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    assert run_bringup("main", tree) == 5


def test_cli_dry_run_and_errors(tree, capsys):
    assert main(["bringup", "--ref", "bringup/test_orders", "--root", str(tree), "--dry-run"]) == 0
    assert "tests/unit/test_orders.py -m '' -n 0" in capsys.readouterr().out
    assert main(["bringup", "--ref", "bringup/", "--root", str(tree)]) == 2
    assert "empty bring-up suffix" in capsys.readouterr().err


BRINGUP_JOB = """
name: Tests
on:
  push:
    branches: [main, "bringup/**"]
jobs:
  offline:
    if: "!startsWith(github.ref, 'refs/heads/bringup/')"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pytest tests/ -n auto
  bringup:
    name: Bring-up run
    if: startsWith(github.ref, 'refs/heads/bringup/')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run the named test once
        run: python -m gate.lanes bringup --ref "$GITHUB_REF_NAME"
"""


def test_bringup_job_lints_clean_and_adds_no_selection_to_the_ci_gate(tmp_path):
    path = tmp_path / "tests.yml"
    path.write_text(textwrap.dedent(BRINGUP_JOB))
    assert lint_workflow(path) == []
    jobs = {invocation.job for invocation in parse_pytest_invocations(path)}
    assert jobs == {"offline"}
