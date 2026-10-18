"""create-pr: pre-flight, title, healing window and the body's five sections."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from tests.tools.slash._commands import claude_md_lists, read_command
from tools.slash import healing, junit, pr

FORK = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def write_log(path, *entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


def entry(minutes: int, applied=True, test="ui/tests/test_a.py::test_x"):
    return {
        "timestamp": (FORK + timedelta(minutes=minutes)).isoformat(),
        "test": test,
        "failure_type": "locator",
        "strategy": "locator_swap",
        "applied": applied,
        "patched_files": ["ui/locators/wiki_locators.json"],
    }


def facts():
    return pr.BranchFacts("feat/x", ["add filter"], ["ui/pages/home_page.py"])


def test_on_the_default_branch_preflight_stops():
    problems = pr.preflight("main", "main", True, 3)
    assert problems and "default or integration branch" in problems[0]
    assert pr.preflight("develop", "main", True, 3)


def test_no_commits_ahead_stops_and_explains():
    problems = pr.preflight("feat/x", "main", True, 0)
    assert problems == ["'feat/x' has no commits ahead of main; there is nothing new to propose"]


def test_unauthenticated_gh_stops():
    assert any("gh CLI" in p for p in pr.preflight("feat/x", "main", False, 2))
    assert pr.preflight("feat/x", "main", True, 2) == []


@pytest.mark.parametrize(
    ("branch", "title"),
    [
        ("feat/search-filters", "feat: search filters"),
        ("feature/date_picker", "feat: date picker"),
        ("bugfix/null-total", "fix: null total"),
        ("spike-on-paging", "spike on paging"),
    ],
)
def test_title_from_branch(branch, title):
    assert pr.title_from_branch(branch) == title


def test_without_xml_the_body_says_results_are_missing(tmp_path):
    body = pr.build_body(facts(), junit.aggregate([]), healing.HealingWindow(False, "no log"))
    assert "Results missing" in body
    assert "0 total" not in body and "0 passed" not in body


@pytest.mark.parametrize("state", ["missing", "empty", "older-only"])
def test_healing_without_entries_in_the_window_is_not_measured(tmp_path, state):
    log = tmp_path / "heal.jsonl"
    if state == "empty":
        log.write_text("", encoding="utf-8")
    elif state == "older-only":
        write_log(log, entry(-30), entry(-1))
    window = healing.read_window(log, FORK, inclusive=False)
    assert not window.measured
    body = pr.build_body(facts(), junit.aggregate([]), window)
    assert "not measured" in body
    assert "0 decision" not in body and "0 applied" not in body


def test_entries_older_than_the_fork_point_are_not_counted(tmp_path):
    log = write_log(tmp_path / "heal.jsonl", entry(-5), entry(0), entry(3), entry(4, applied=False))
    window = healing.read_window(log, FORK, inclusive=False)
    assert window.measured and len(window.entries) == 2 and len(window.applied) == 1
    assert "2 decision(s), 1 applied" in healing.describe(window)


def test_the_body_has_all_five_sections(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<testsuite><testcase classname="t" name="a"/><testcase classname="t" name="b">'
        '<error message="boom"/></testcase></testsuite>',
        encoding="utf-8",
    )
    window = healing.read_window(write_log(tmp_path / "h.jsonl", entry(1)), FORK)
    body = pr.build_body(facts(), junit.aggregate([xml]), window)
    positions = [body.index(section) for section in pr.SECTIONS]
    assert positions == sorted(positions)
    assert "2 total, 1 passed, 1 failed" in body and "BLOCK" in body
    assert "Locator files changed by healing are committed" in body


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_fork_point_time_reads_the_merge_base(tmp_path):
    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            env={"GIT_COMMITTER_DATE": "2026-10-01T12:00:00+00:00", "PATH": "/usr/bin:/bin"},
        )

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "base")
    git("checkout", "-q", "-b", "feat/x")
    assert healing.fork_point_time("main", cwd=tmp_path) == FORK


def test_the_command_is_manual_only_and_listed():
    meta, body = read_command("create-pr")
    assert meta["disable-model-invocation"] is True
    assert claude_md_lists("create-pr")
    for promise in ("preflight", "not measured", "missing", "gh pr create", "five sections"):
        assert promise in body, promise
    assert body.index("preflight") < body.index("git push")
