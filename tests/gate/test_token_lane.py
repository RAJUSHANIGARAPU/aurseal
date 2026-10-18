"""The opt-in bearer-token lane (gate.token_lane) and its render checks' abstention."""

from __future__ import annotations

import shlex
import socket
from pathlib import Path

import pytest
import yaml

from gate.evidence import merge_junit_files
from gate.lanes import excluded_markers
from gate.token_lane import MARKER, build_command, child_env, guard, main, should_run
from tests.render_lane.conftest import external_target

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "tok-lane-not-real-55"


def test_selection():
    assert should_run("workflow_dispatch", "main", flag=True)[0]
    assert not should_run("workflow_dispatch", "main", flag=False)[0]
    assert should_run("schedule", "refs/heads/main", token_present=True)[0]
    run, reason = should_run("schedule", "refs/heads/main", token_present=False)
    assert not run and "RENDER_TOKEN" in reason
    assert not should_run("push", "refs/heads/main", flag=True, token_present=True)[0]


def test_guard_refuses_without_token_mode_or_token():
    assert "auth mode 'token'" in guard("none", TOKEN)
    assert "needs a bearer token" in guard("token", "  ")
    assert guard("token", TOKEN) is None
    spawned = []
    environ = {"AUTH_MODE": "ui", "SUPPLIED_TOKEN": TOKEN}
    assert main(["run"], environ=environ, spawn=lambda *a: spawned.append(a) or 0) == 2
    assert main(["run"], environ={"AUTH_MODE": "token"}, spawn=lambda *a: spawned.append(a)) == 2
    assert spawned == []


def test_command_and_child_environment():
    command = build_command()
    assert MARKER in command and not any(a.startswith("--reruns") for a in command)
    assert "--alluredir=reports/allure-results-render" in command
    assert "--junitxml=reports/junit-render.xml" in command
    seen = {}
    code = main(
        ["run"],
        environ={"AUTH_MODE": "token", "SUPPLIED_TOKEN": TOKEN},
        spawn=lambda argv, env: seen.update(argv=argv, env=env) or 0,
    )
    assert code == 0 and TOKEN not in " ".join(seen["argv"])
    assert seen["env"]["SUPPLIED_TOKEN"] == TOKEN and seen["env"]["RENDER_REQUIRED"] == "true"
    assert child_env(TOKEN, {})["RUN_TRUST_LANE"] == "render"


def test_the_workflow_step_is_the_built_command_and_the_gate_excludes_the_marker():
    jobs = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())["jobs"]
    step = next(s for s in jobs["render"]["steps"] if s.get("name") == "Render checks")
    assert shlex.split(step["run"]) == build_command()
    assert TOKEN not in step["run"] and "SUPPLIED_TOKEN" in step["env"]
    assert MARKER in excluded_markers({})
    guard_step = jobs["render"]["steps"].index(
        next(s for s in jobs["render"]["steps"] if "token_lane guard" in str(s.get("run")))
    )
    assert guard_step < jobs["render"]["steps"].index(step)


def test_same_named_junit_files_from_two_lanes_both_survive(tmp_path):
    for lane in ("browser", "render"):
        reports = tmp_path / "in" / f"evidence-{lane}" / "reports"
        reports.mkdir(parents=True)
        (reports / "junit.xml").write_text(f"<testsuite name='{lane}'/>")
    artefacts = {lane: tmp_path / "in" / f"evidence-{lane}" for lane in ("browser", "render")}
    merge = merge_junit_files(artefacts, tmp_path / "out")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "evidence-render--junit.xml",
        "junit.xml",
    ]
    assert merge.renames


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_required_lane_with_an_unreachable_renderer_fails_instead_of_skipping(monkeypatch):
    url = f"http://127.0.0.1:{_closed_port()}"
    environ = {"SUPPLIED_TOKEN": TOKEN, "RENDER_BASE_URL": url}
    monkeypatch.setenv("RENDER_REQUIRED", "true")
    with pytest.raises(pytest.fail.Exception, match="unreachable"):
        external_target(environ)
    monkeypatch.setenv("RENDER_REQUIRED", "false")
    with pytest.raises(pytest.skip.Exception):
        external_target(environ)
