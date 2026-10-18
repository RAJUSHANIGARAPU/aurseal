"""Offline workflow lint: this repo's workflows pass, and each rule can fail."""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from gate.workflow_lint import (
    actionlint_check,
    lint_workflow,
    lint_workflows,
    main,
    parse_check,
)

ROOT = Path(__file__).resolve().parents[2]

GOOD = """
name: CI
on: [push]
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
env:
  TZ: UTC
jobs:
  offline:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - name: Test
        run: pytest tests/ -n auto
  browser:
    needs: offline
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - run: pytest -m smoke
  reusable:
    uses: ./.github/workflows/other.yml
"""


def _lint(tmp_path: Path, body: str, required=()):
    path = tmp_path / "ci.yml"
    path.write_text(textwrap.dedent(body))
    return [f.problem for f in lint_workflow(path, required)]


def test_this_repository_workflows_pass():
    findings = lint_workflows(ROOT / ".github" / "workflows", {"tests.yml": ["offline"]})
    assert findings == [], "\n".join(map(str, findings))


def test_a_good_workflow_has_no_findings(tmp_path):
    assert _lint(tmp_path, GOOD, required=["offline"]) == []


@pytest.mark.parametrize(
    ("mutation", "problem"),
    [
        (("  TZ: UTC", "  TZ:"), "env TZ has no value"),
        (("on: [push]\n", ""), "has no 'on' trigger"),
        (("    needs: offline", "    needs: [offline, ghost]"), "needs unknown job 'ghost'"),
        (("  offline:\n    runs-on: ubuntu-latest\n", "  offline:\n"), "has no runs-on"),
        (
            ("      - run: pytest -m smoke", "      - name: nothing"),
            "needs exactly one of run/uses",
        ),
        (
            ("      - run: pytest -m smoke", "      - run: pytest -m smoke || true"),
            "masks the pytest exit status: pytest -m smoke || true",
        ),
        (
            ("      - run: pytest -m smoke", "      - run: pytest -m smoke || [ $? -eq 5 ]"),
            "masks the pytest exit status: pytest -m smoke || [ $? -eq 5 ]",
        ),
        (
            ("      - run: pytest -m smoke", "      - run: echo ${{ github.sha"),
            "unbalanced ${{ }} expression",
        ),
    ],
)
def test_each_rule_can_fail(tmp_path, mutation, problem):
    old, new = mutation
    assert old in GOOD
    assert problem in _lint(tmp_path, GOOD.replace(old, new))


def test_required_job_missing(tmp_path):
    assert _lint(tmp_path, GOOD, required=["deploy"]) == ["required job is missing"]


def test_unparseable_and_empty_files(tmp_path):
    assert _lint(tmp_path, "jobs: [unclosed")[0].startswith("does not parse")
    assert _lint(tmp_path, "- just a list") == ["is not a mapping"]
    assert _lint(tmp_path, "on: push\n") == ["has no jobs"]


def test_required_workflow_missing(tmp_path):
    (tmp_path / "ci.yml").write_text(textwrap.dedent(GOOD))
    findings = lint_workflows(tmp_path, {"release.yml": []}, shape=None)
    assert [f.problem for f in findings] == ["required workflow is missing"]


# ---------------------------------------------------------------------------
# local parse check and the commit guard
# ---------------------------------------------------------------------------


def test_parse_check_statuses(tmp_path):
    good = tmp_path / "good.yml"
    good.write_text(textwrap.dedent(GOOD))
    bad = tmp_path / "bad.yml"
    bad.write_text("jobs: [unclosed")
    assert parse_check(good).status == "ok" and parse_check(good).exit_code == 0
    assert parse_check(bad).status == "failed" and parse_check(bad).exit_code == 1
    missing = parse_check(tmp_path / "gone.yml")
    assert missing.status == "missing" and missing.exit_code == 2


def test_actionlint_abstains_when_not_installed():
    result = actionlint_check(["x.yml"], which=lambda _: None)
    assert result.status == "abstained" and result.exit_code == 3
    assert "not installed" in result.detail


def test_actionlint_result_is_passed_through():
    def runner(stdout, code):
        return lambda cmd, **_: subprocess.CompletedProcess(cmd, code, stdout, "")

    found = actionlint_check(["x.yml"], which=lambda _: "/bin/al", runner=runner("x.yml:3: bad", 1))
    assert found.status == "failed" and found.findings[0].problem == "x.yml:3: bad"
    assert actionlint_check(["x.yml"], lambda _: "/bin/al", runner("", 0)).status == "ok"
    silent = actionlint_check(["x.yml"], lambda _: "/bin/al", runner("", 2))
    assert silent.status == "failed" and silent.findings


def test_cli_exit_codes(tmp_path, capsys):
    good = tmp_path / "good.yml"
    good.write_text(textwrap.dedent(GOOD))
    bad = tmp_path / "bad.yml"
    bad.write_text(textwrap.dedent(GOOD).replace("  TZ: UTC", "  TZ:"))
    assert main([str(good)]) == 0
    assert "1 file(s) clean" in capsys.readouterr().out
    assert main([str(good), str(bad)]) == 1
    assert "env TZ has no value" in capsys.readouterr().out
    assert main([str(tmp_path / "gone.yml")]) == 2
    assert main(["--dir", str(tmp_path / "none")]) == 0


def test_cli_actionlint_abstention_is_not_green(tmp_path, monkeypatch):
    good = tmp_path / "good.yml"
    good.write_text(textwrap.dedent(GOOD))
    monkeypatch.setattr("gate.workflow_lint.shutil.which", lambda _: None)
    assert main([str(good), "--actionlint"]) == 3


def test_cli_runs_as_a_module(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text("jobs: [unclosed")
    completed = subprocess.run(
        [sys.executable, "-m", "gate.workflow_lint", str(bad)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1 and "does not parse" in completed.stdout


def _hooks() -> dict[str, dict]:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    return {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}


def test_commit_guard_hook_is_scoped_to_workflows():
    hook = _hooks()["workflow-lint"]
    assert hook["entry"] == "python -m gate.workflow_lint"
    assert hook["pass_filenames"] is True
    files = re.compile(hook["files"])
    assert files.search(".github/workflows/tests.yml")
    assert files.search(".github/workflows/release.yaml")
    assert not files.search("docs/workflows/tests.yml")
    assert not files.search("gate/workflow_lint.py")


def test_actionlint_hook_is_registered():
    assert "actionlint" in _hooks()
