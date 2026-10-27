"""Docs-only changes skip the browser jobs (gate.change_scope); anything unproven runs all."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from gate.change_scope import (
    DOCS_ONLY_PATTERNS,
    DiffUnavailable,
    changed_paths,
    classify,
    classify_event,
    main,
    path_matches,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
SKIPPED_ON_DOCS = ("browser", "visual", "platform", "browser-matrix")


# ------------------------------------------------------------------ globs


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("docs/a.md", "docs/**", True),
        ("docs/deep/er/a.png", "docs/**", True),
        ("README.md", "*.md", True),
        ("specs/plan.md", "*.md", False),  # one level only
        ("tests/conftest.py", "**/conftest.py", True),
        ("conftest.py", "**/conftest.py", True),
        ("requirements-docs.txt", "requirements*.txt", True),
        ("./docs/a.md", "docs/**", True),
        ("docsx/a.md", "docs/**", False),
        ("a.py", "?.py", True),
    ],
)
def test_path_globs(path, pattern, expected):
    assert path_matches(path, pattern) is expected


# ------------------------------------------------------------------ classification


def test_only_docs_is_docs_only():
    verdict = classify(["docs/ci-decisions.md", "README.md", ".claude/commands/x.md"])
    assert verdict.docs_only and "3 path(s)" in verdict.reason


def test_one_code_path_makes_it_a_full_run_and_names_the_path():
    verdict = classify(["docs/a.md", "gate/lanes.py"])
    assert not verdict.docs_only
    assert verdict.other_paths == ("gate/lanes.py",) and "gate/lanes.py" in verdict.reason


def test_markdown_inside_a_package_is_not_documentation():
    """Specs and plans are Markdown that code reads."""
    assert not classify(["specs/search.md"]).docs_only


def test_no_changed_paths_is_never_docs_only():
    assert not classify([]).docs_only
    assert not classify(["", "  "]).docs_only


def test_the_list_is_the_documented_one():
    assert DOCS_ONLY_PATTERNS == ("docs/**", "*.md", ".claude/**")


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({"CHANGE_EVENT": "schedule"}, "has no change set"),
        ({"CHANGE_EVENT": "workflow_dispatch"}, "has no change set"),
        ({"CHANGE_EVENT": "push", "CHANGE_BASE": "0" * 40, "CHANGE_HEAD": "abc"}, "no base"),
        ({"CHANGE_EVENT": "pull_request", "CHANGE_HEAD": "abc"}, "no base"),
    ],
)
def test_events_without_a_change_set_run_everything(env, reason):
    verdict = classify_event(env, ROOT)
    assert not verdict.docs_only and reason in verdict.reason


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_a_real_diff_is_classified(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "app.py").write_text("x = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "docs")
    docs_head = _git(tmp_path, "rev-parse", "HEAD")
    env = {"CHANGE_EVENT": "push", "CHANGE_BASE": base, "CHANGE_HEAD": docs_head}
    assert classify_event(env, tmp_path).docs_only
    (tmp_path / "app.py").write_text("x = 2\n")
    _git(tmp_path, "commit", "-qam", "code")
    env["CHANGE_HEAD"] = _git(tmp_path, "rev-parse", "HEAD")
    assert not classify_event(env, tmp_path).docs_only


def test_an_unknown_commit_is_a_diff_failure_not_a_verdict(tmp_path):
    _git(tmp_path, "init", "-q")
    with pytest.raises(DiffUnavailable, match="git diff failed"):
        changed_paths("deadbeef", "HEAD", tmp_path)
    env = {"CHANGE_EVENT": "push", "CHANGE_BASE": "deadbeef", "CHANGE_HEAD": "HEAD"}
    assert not classify_event(env, tmp_path).docs_only


def test_cli_writes_the_output_and_a_summary_line(tmp_path, capsys):
    output, summary = tmp_path / "out", tmp_path / "summary"
    args = ["classify", "--github-output", str(output), "--summary", str(summary)]
    assert main([*args, "--paths", "docs/a.md"], env={}) == 0
    assert output.read_text() == "docs_only=true\n"
    assert main([*args, "--paths", "docs/a.md", "ui/x.py"], env={}) == 0
    assert output.read_text().splitlines()[-1] == "docs_only=false"
    assert "Change scope: full run" in summary.read_text()
    assert main(["classify"], env={"CHANGE_EVENT": "schedule"}) == 0
    assert "has no change set" in capsys.readouterr().out


# ------------------------------------------------------------------ workflow wiring


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def test_the_classifier_job_exports_docs_only():
    job = _jobs()["changes"]
    assert job["outputs"]["docs_only"] == "${{ steps.classify.outputs.docs_only }}"
    step = next(s for s in job["steps"] if s.get("id") == "classify")
    assert step["run"] == "python -m gate.change_scope classify"
    assert set(step["env"]) == {"CHANGE_EVENT", "CHANGE_BASE", "CHANGE_HEAD"}
    checkout = job["steps"][0]
    assert checkout["with"]["fetch-depth"] == 0  # a shallow clone cannot diff


@pytest.mark.parametrize("name", SKIPPED_ON_DOCS)
def test_browser_jobs_skip_on_docs_only_with_a_job_if(name):
    job = _jobs()[name]
    assert "changes" in job["needs"] and "offline" in job["needs"]
    assert job["if"].rstrip('"').endswith("needs.changes.outputs.docs_only != 'true'")


def test_the_offline_suite_never_reads_the_classifier():
    jobs = _jobs()
    assert "docs_only" not in yaml.safe_dump(jobs["offline"])
    assert "needs" not in jobs["offline"]


def test_no_trigger_uses_paths_ignore():
    """paths-ignore would leave a required check pending forever on a docs-only change."""
    data = yaml.safe_load(WORKFLOW.read_text())
    triggers = data.get("on", data.get(True))
    for event in ("push", "pull_request"):
        assert "paths-ignore" not in triggers[event] and "paths" not in triggers[event]
