"""The protected-branch push guard (tools.push_guard)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.push_guard import (
    BLOCK_EXIT,
    blocked,
    deny_list,
    hook,
    merged_pr,
    pre_push,
    pushed_remote_refs,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("command", "branch"),
    [
        ("git push origin main", "feat/x"),
        ("git push -f origin main", "feat/x"),
        ("git push upstream HEAD:main", "feat/x"),
        ("git push origin +main", "feat/x"),
        ("git push origin HEAD:refs/heads/main", "feat/x"),
        ("cd x && git push origin main", "feat/x"),
        ("git push", "main"),
        ("git push origin", "main"),
        ("git push origin :main", "feat/x"),
        ("git push --force-with-lease origin main", "feat/x"),
        ("git -C repo push origin main --force", "feat/x"),
        ("git push origin HEAD", "main"),
        ("git push --all origin", "feat/x"),
        ("make lint; git push origin main", "feat/x"),
    ],
)
def test_blocked(command, branch):
    assert blocked(command, branch, "main")


@pytest.mark.parametrize(
    ("command", "branch"),
    [
        ("git push origin feat/x", "feat/x"),
        ("git push origin HEAD:feat/x", "main"),
        ("git push origin main-docs", "feat/x"),
        ("git log main", "main"),
        ('echo "git push origin main"', "feat/x"),
        ("git push", "feat/x"),
        ("git push origin HEAD", "feat/x"),
    ],
)
def test_allowed(command, branch):
    assert blocked(command, branch, "main") is None


def test_branch_name_comes_from_configuration(monkeypatch):
    monkeypatch.setenv("PUSH_GUARD_BRANCH", "trunk")
    assert blocked("git push origin trunk", "x")
    assert blocked("git push origin main", "x") is None


def test_deny_list_covers_remotes_head_forms_and_force_variants():
    entries = set(deny_list("main"))
    for expected in (
        "Bash(git push origin main)",
        "Bash(git push origin main *)",
        "Bash(git push * main)",
        "Bash(git push origin HEAD:main)",
        "Bash(git push * HEAD:refs/heads/main *)",
        "Bash(git push -f origin main)",
        "Bash(git push --force * main *)",
        "Bash(git push origin main --force-with-lease)",
    ):
        assert expected in entries


def test_hook_protocol():
    payload = {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}
    code, message = hook(json.dumps(payload))
    assert code == BLOCK_EXIT and message.startswith("push guard:")
    assert hook(json.dumps({"tool_name": "Read", "tool_input": {}})) == (0, "")


@pytest.mark.parametrize(
    ("command", "expected"), [("git push origin main", 2), ("git push origin feat/x", 0)]
)
def test_cli_reads_the_hook_json_from_stdin(command, expected):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    done = subprocess.run(
        [sys.executable, "-m", "tools.push_guard"],
        input=payload,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == expected, done.stderr
    assert bool(done.stderr.strip()) == bool(expected)


# ---------------------------------------------------------------- pre-push hook

ZERO = "0" * 40
SHA = "c" * 40


@pytest.mark.parametrize(
    "line",
    [
        f"refs/heads/feat/x {SHA} refs/heads/main {ZERO}",
        f"refs/heads/main {SHA} refs/heads/main {SHA}",
        f"(delete) {ZERO} refs/heads/main {SHA}",  # a delete of main
    ],
)
def test_pre_push_blocks_a_push_to_main_from_git_stdin(line):
    code, message = pre_push(line + "\n", environ={}, branch="main")
    assert code == 1 and "open a pull request" in message


def test_pre_push_checks_every_ref_of_a_multi_ref_push():
    lines = f"refs/heads/a {SHA} refs/heads/a {ZERO}\nrefs/heads/b {SHA} refs/heads/main {ZERO}\n"
    assert pre_push(lines, environ={}, branch="main")[0] == 1


def test_pre_push_allows_other_branches_and_reads_pre_commit_env():
    assert pre_push(f"refs/heads/x {SHA} refs/heads/main-docs {ZERO}\n", {}, "main") == (0, "")
    env = {"PRE_COMMIT_REMOTE_BRANCH": "refs/heads/main"}
    assert pre_push("", env, "main")[0] == 1
    assert pre_push("", {"PRE_COMMIT_REMOTE_BRANCH": "refs/heads/feat/y"}, "main") == (0, "")
    assert pushed_remote_refs("", {}) == []


def test_pre_push_cli_blocks_with_exit_1():
    done = subprocess.run(
        [sys.executable, "-m", "tools.push_guard", "pre-push"],
        input=f"refs/heads/feat/x {SHA} refs/heads/main {ZERO}\n",
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT)},
    )
    assert done.returncode == 1 and "push guard" in done.stderr


def test_pre_commit_wires_the_guards_and_the_rules_lint():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    assert "pre-push" in config["default_install_hook_types"]
    hooks = {h["id"]: h for repo in config["repos"] for h in repo["hooks"]}
    assert hooks["push-guard"]["stages"] == ["pre-push"]
    assert hooks["push-guard"]["always_run"] is True
    assert hooks["push-guard"]["pass_filenames"] is False
    assert hooks["no-commit-to-branch"]["args"] == ["--branch", "main"]
    assert hooks["rules-lint"]["entry"] == "python -m tools.rules_lint"


# ---------------------------------------------------------------- detective CI check


def _fetch(answers):
    calls = []

    def fetch(url, headers):
        calls.append((url, dict(headers)))
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return fetch, calls


def test_merged_pr_passes_for_a_merged_pull_request():
    fetch, calls = _fetch([[{"number": 12, "merged_at": "2031-01-01T00:00:00Z"}]])
    code, message = merged_pr("o/r", SHA, {"GITHUB_TOKEN": "t"}, fetch, sleep=lambda _s: None)
    assert code == 0 and "#12" in message
    url, headers = calls[0]
    assert url == f"https://api.github.com/repos/o/r/commits/{SHA}/pulls"
    assert headers["Authorization"] == "Bearer t"


def test_merged_pr_fails_a_direct_push_after_asking_again():
    slept = []
    fetch, calls = _fetch([[{"number": 3, "merged_at": None}]])
    code, message = merged_pr("o/r", SHA, {}, fetch, attempts=3, sleep=slept.append)
    assert code == 1 and "direct push" in message
    assert len(calls) == 3 and len(slept) == 2


def test_merged_pr_waits_out_a_lagging_association():
    fetch, _calls = _fetch([[], [{"number": 4, "merged_at": "x"}]])
    assert merged_pr("o/r", SHA, {}, fetch, sleep=lambda _s: None)[0] == 0


def test_merged_pr_unreachable_api_is_not_a_pass():
    fetch, _calls = _fetch([OSError("down")])
    assert merged_pr("o/r", SHA, {}, fetch, sleep=lambda _s: None)[0] == 2
    fetch, _calls = _fetch([{"message": "Not Found"}])
    assert merged_pr("o/r", SHA, {}, fetch, sleep=lambda _s: None)[0] == 2


def test_the_detective_workflow_runs_on_every_main_push():
    flow = yaml.safe_load((ROOT / ".github" / "workflows" / "main-push-check.yml").read_text())
    on = flow.get("on", flow.get(True))
    assert on == {"push": {"branches": ["main"]}}
    job = flow["jobs"]["merged-pr"]
    assert "if" not in job
    assert "tools.push_guard merged-pr" in job["steps"][-1]["run"]
