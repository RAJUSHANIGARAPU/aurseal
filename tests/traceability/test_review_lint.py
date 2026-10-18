"""The lint catches a generator that forgot the marker; git history records approvals."""

from __future__ import annotations

import subprocess

import pytest

from traceability.cli import main as trace_main
from traceability.review import (
    generated_paths,
    is_draft_source,
    lint_generated,
    mark_draft,
    unmark_draft,
)

CODE = "import pytest\n\n\ndef test_x():\n    assert True\n"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "dev@example.test")
    _git(tmp_path, "config", "user.name", "Dev")
    (tmp_path / "gen").mkdir()
    return tmp_path


def test_an_unmarked_generated_file_fails(repo):
    (repo / "gen" / "test_a.py").write_text(CODE)
    findings = lint_generated([repo / "gen"], cwd=repo)
    assert [f.path.endswith("test_a.py") for f in findings] == [True]


def test_a_marked_file_passes(repo):
    (repo / "gen" / "test_a.py").write_text(mark_draft(CODE, "spec", "s.md"))
    assert lint_generated([repo / "gen"], cwd=repo) == []


def test_a_reviewed_removal_in_history_is_an_approval(repo):
    path = repo / "gen" / "test_a.py"
    path.write_text(mark_draft(CODE, "spec", "s.md"))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "generated")
    path.write_text(unmark_draft(path.read_text()))
    # removed in the working tree but not committed: not yet approved
    assert len(lint_generated([repo / "gen"], cwd=repo)) == 1
    _git(repo, "commit", "-q", "-am", "approve after review")
    assert lint_generated([repo / "gen"], cwd=repo) == []


def test_no_git_is_reported_not_passed(tmp_path):
    (tmp_path / "test_a.py").write_text(CODE)
    findings = lint_generated([tmp_path], cwd=tmp_path)
    assert findings and "cannot" in findings[0].problem


def test_mark_draft_is_idempotent_and_reversible():
    once = mark_draft(CODE, "trace", "t.zip", "T")
    assert mark_draft(once, "trace", "t.zip", "T") == once
    assert is_draft_source(once) and unmark_draft(once) == CODE
    bad = "def broken(:\n"
    assert mark_draft(bad, "trace", "t.zip") == bad


def test_paths_from_the_environment():
    assert [str(p) for p in generated_paths({"GENERATED_TEST_PATHS": "a:b"})] == ["a", "b"]
    assert [str(p) for p in generated_paths({})] == ["generated_tests"]


def test_cli_lint(repo, capsys, monkeypatch):
    monkeypatch.chdir(repo)
    (repo / "gen" / "test_a.py").write_text(CODE)
    assert trace_main(["review", "lint", "gen"]) == 1
    (repo / "gen" / "test_a.py").write_text(mark_draft(CODE, "spec", "s.md"))
    assert trace_main(["review", "lint", "gen"]) == 0
