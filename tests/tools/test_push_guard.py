"""The protected-branch push guard (tools.push_guard)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.push_guard import BLOCK_EXIT, blocked, deny_list, hook

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
