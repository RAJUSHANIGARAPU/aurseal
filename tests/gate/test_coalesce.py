"""Coalescing window (gate.coalesce): off by default, only for default-branch pushes, and any
doubt runs the gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from gate.coalesce import MAX_SECONDS, decide, main, window_seconds
from gate.workflow_lint import DEFAULT_SHAPE, critical_path

ROOT = Path(__file__).resolve().parents[2]
OWN = "a" * 40
NEWER = "b" * 40
PUSH = {
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": OWN,
    "COALESCE_SECONDS": "30",
}


def _ls_remote(sha: str, code: int = 0):
    def run(cmd, **_kw):
        assert cmd[:3] == ["git", "ls-remote", "origin"]
        return subprocess.CompletedProcess(cmd, code, f"{sha}\trefs/heads/main\n", "boom")

    return run


def test_off_by_default_and_never_sleeps():
    slept = []
    decision = decide({**PUSH, "COALESCE_SECONDS": ""}, sleep=slept.append)
    assert not decision.superseded and "off" in decision.reason and slept == []


def test_window_is_clamped_and_unreadable_is_off():
    assert window_seconds({"COALESCE_SECONDS": "9999"}) == MAX_SECONDS
    assert window_seconds({"COALESCE_SECONDS": "-5"}) == 0
    assert window_seconds({"COALESCE_SECONDS": "soon"}) == 0


def test_a_newer_head_supersedes_this_run():
    slept = []
    decision = decide(PUSH, sleep=slept.append, run=_ls_remote(NEWER))
    assert decision.superseded and slept == [30]
    assert decision.reason == f"superseded by {NEWER}, covered by that run"


def test_still_the_newest_commit_runs_the_gate():
    assert not decide(PUSH, sleep=lambda _s: None, run=_ls_remote(OWN)).superseded


def test_any_error_runs_the_gate():
    assert not decide(PUSH, sleep=lambda _s: None, run=_ls_remote(NEWER, code=128)).superseded

    def explode(*_a, **_k):
        raise OSError("no git")

    assert not decide(PUSH, sleep=lambda _s: None, run=explode).superseded
    assert not decide(PUSH, sleep=lambda _s: None, run=_ls_remote("short")).superseded


def test_never_on_dispatch_schedule_pull_request_or_another_branch():
    for event, ref in (
        ("workflow_dispatch", "refs/heads/main"),
        ("schedule", "refs/heads/main"),
        ("pull_request", "refs/pull/3/merge"),
        ("push", "refs/heads/feature/x"),
    ):
        env = {**PUSH, "GITHUB_EVENT_NAME": event, "GITHUB_REF": ref}
        decision = decide(env, sleep=lambda _s: _must_not_sleep(), run=_ls_remote(NEWER))
        assert not decision.superseded


def _must_not_sleep():  # a sleep here would mean the window applied where it must not
    raise AssertionError("the window applied outside a default-branch push")


def test_cli_writes_the_output_and_the_summary(tmp_path):
    output, summary = tmp_path / "out", tmp_path / "summary"
    env = {**PUSH, "COALESCE_SECONDS": "0"}
    env.update(GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary))
    assert main([], environ=env) == 0
    assert output.read_text() == "superseded=false\n" and not summary.exists()


def test_the_window_job_is_first_bounded_and_on_the_budget():
    flow = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())
    jobs = flow["jobs"]
    window = jobs["coalesce"]
    assert isinstance(window["timeout-minutes"], int)
    assert window["timeout-minutes"] * 60 >= MAX_SECONDS + 60
    assert "github.event_name == 'push'" in window["if"]
    assert "refs/heads/main" in window["if"]
    assert window["steps"][-1]["continue-on-error"] is True  # an error runs the gate
    assert jobs["offline"]["needs"] == "coalesce"
    assert "!cancelled()" in jobs["offline"]["if"]
    assert "needs.coalesce.outputs.superseded != 'true'" in jobs["offline"]["if"]
    path, minutes = critical_path(jobs)
    assert path[0] == "coalesce" and minutes <= DEFAULT_SHAPE.run_budget_minutes
