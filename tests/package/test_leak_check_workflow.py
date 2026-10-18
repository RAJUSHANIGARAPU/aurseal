"""The leak-check workflow: its shape, and its scan step run for real on planted terms.

The scan step's script is taken from the workflow file and executed with bash against a
throwaway repository, so what is tested is what CI runs. Terms are synthetic.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from gate.workflow_lint import DEFAULT_SHAPE, lint_workflow

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "leak-check.yml"
TERM = "zorblax"
LISTING = f"term:{TERM}\nqual:frobnitz_server.ledger\n"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git"
)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _scan_script() -> str:
    steps = _workflow()["jobs"]["leak-check"]["steps"]
    return next(s["run"] for s in steps if s.get("name") == "Scan")


def test_the_workflow_passes_the_repository_lint():
    assert lint_workflow(WORKFLOW, shape=DEFAULT_SHAPE) == []


def test_the_workflow_never_exposes_the_secret_to_untrusted_triggers_or_scripts():
    data = _workflow()
    triggers = data.get("on", data.get(True))
    assert "pull_request_target" not in triggers
    for step in data["jobs"]["leak-check"]["steps"]:
        script = str(step.get("run") or "")
        assert "${{" not in script, f"expression inside a script: {step.get('name')}"
        assert "set -x" not in script


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t",
         "-c", "user.email=t@example.com", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("ACME Shop tests\n", "utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "first")
    return path


def _run_step(repo: Path, tmp_path: Path, **env: str) -> tuple[int, str, str]:
    temp = tmp_path / "runner-temp"
    temp.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    summary = tmp_path / "summary.md"
    environ = {
        "PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""),
        "HOME": str(home),
        "RUNNER_TEMP": str(temp),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_REPOSITORY": "owner/repo",
        "LEAK_DENYLIST": "",
        "EVENT": "push",
        "PR_TITLE": "",
        "PR_BODY": "",
        "BASE_SHA": "",
        "HEAD_SHA": "",
        "HEAD_REPO": "",
        "DISPATCH_PR": "",
        "ACTOR": "someone",
        **env,
    }
    result = subprocess.run(
        ["bash", "-e", "-c", _scan_script()],
        cwd=repo,
        env=environ,
        capture_output=True,
        text=True,
    )
    text = summary.read_text("utf-8") if summary.exists() else ""
    return result.returncode, result.stdout + result.stderr, text


def test_a_planted_term_fails_the_scan_without_echoing_it(repo, tmp_path):
    (repo / "notes.md").write_text(f"intro\nported from {TERM.title()} last week\n", "utf-8")
    code, output, _ = _run_step(repo, tmp_path, LEAK_DENYLIST=LISTING)
    assert code == 1
    assert "notes.md:2 deny.term [e1]" in output
    assert TERM not in output.lower()
    assert "ported from" not in output


def test_a_pull_request_scans_title_body_messages_and_added_lines(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("from frobnitz_server.ledger import post\n", "utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", f"copy the {TERM} fix")
    head = _git(repo, "rev-parse", "HEAD")
    code, output, summary = _run_step(
        repo,
        tmp_path,
        LEAK_DENYLIST=LISTING,
        EVENT="pull_request",
        PR_TITLE=f"Port {TERM} helpers",
        PR_BODY=f"Summary\n\nSame as in {TERM}.",
        BASE_SHA=base,
        HEAD_SHA=head,
        HEAD_REPO="owner/repo",
    )
    assert code == 1, output
    for location in ("pr-title:1", "pr-body:3", "app.py:1 deny.qual", " msg:1 deny.term"):
        assert location in output
    assert TERM not in output.lower() and "frobnitz" not in output
    assert "findings" in summary


def test_a_fork_pull_request_without_the_secret_is_not_assessed(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "docs.md").write_text("more docs\n", "utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "docs")
    code, output, summary = _run_step(
        repo,
        tmp_path,
        EVENT="pull_request",
        BASE_SHA=base,
        HEAD_SHA=_git(repo, "rev-parse", "HEAD"),
        HEAD_REPO="outsider/repo",
    )
    assert code == 0, output
    assert "NOT ASSESSED" in output and "NOT ASSESSED" in summary


def test_a_branch_of_this_repository_without_the_secret_fails_closed(repo, tmp_path):
    code, output, _ = _run_step(repo, tmp_path)
    assert code == 2
    assert "cannot run" in output


def test_the_weekly_scan_checks_history_against_the_denylist(repo, tmp_path):
    (repo / "old.md").write_text(f"{TERM}\n", "utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add")
    (repo / "old.md").unlink()
    _git(repo, "commit", "-qam", "remove")  # gone from the tree, still in history
    code, output, _ = _run_step(repo, tmp_path, LEAK_DENYLIST=LISTING, EVENT="schedule")
    assert code == 1
    assert "old.md:1 deny.term [e1]" in output and TERM not in output.lower()
