"""The ignore policy: secrets, run artefacts and local session data can never be committed
by accident, while the deliberately shared files stay tracked. The tables are the spec."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

IGNORED = (
    ".env",
    ".env.qa",
    "config/env/.env.staging",
    "reports/allure-results/x-result.json",
    "reports/traces/t.zip",
    "reports/videos/v.webm",
    "reports/screenshots/s.png",
    ".cache/test-auth/login.token",
    "ui/locators/search.heals-pending.json",
    "ext-config/demo_sut/config/subscription.machine.yaml",
    "evidence/reports/run-trust.txt",
    "docs/local-notes.md",
    "session.handoff-draft.md",
    ".claude/settings.local.json",
    ".claude/projects/x.json",
    "gate/__pycache__/lanes.cpython-311.pyc",
    "x.pyc",
)
TRACKED = (
    ".env.example",
    "config/env/.env.example",
    ".claude/commands/analyze-trace.md",
    ".claude/settings.json",
    ".claude/rules/x.md",
    ".claude/hooks/push_guard.py",
    "docs/artefacts.md",
)


def _ignored(path: str) -> bool:
    done = subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=ROOT, check=False)
    assert done.returncode in (0, 1), f"git check-ignore failed for {path}"
    return done.returncode == 0


@pytest.mark.parametrize("path", IGNORED)
def test_is_ignored(path):
    assert _ignored(path)


@pytest.mark.parametrize("path", TRACKED)
def test_is_tracked(path):
    assert not _ignored(path)


def test_no_tracked_file_matches_an_ignore_rule():
    done = subprocess.run(
        ["git", "ls-files", "-i", "-c", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.strip() == ""


def test_a_new_rules_file_can_be_staged(tmp_path):
    """In a scratch repository with this .gitignore, `git add` stages a rules file."""
    (tmp_path / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    rules = tmp_path / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "x.md").write_text("---\npaths: ['x']\n---\n")
    (tmp_path / ".claude" / "settings.local.json").write_text("{}")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert ".claude/rules/x.md" in staged
    assert ".claude/settings.local.json" not in staged
