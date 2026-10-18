"""The run's commit resolves by reading files: no git binary needed."""

from __future__ import annotations

import pytest

from results.revision import run_git_sha

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "89abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def no_git_on_path(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("GITHUB_SHA", raising=False)


def _repo(tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    return git


def test_github_sha_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", SHA)
    assert run_git_sha(tmp_path) == SHA


def test_loose_ref(tmp_path):
    git = _repo(tmp_path)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text(SHA + "\n")
    assert run_git_sha(tmp_path / "sub" / "dir") == SHA


def test_packed_ref(tmp_path):
    git = _repo(tmp_path)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "packed-refs").write_text(f"# pack-refs with: peeled\n{SHA} refs/heads/main\n")
    assert run_git_sha(tmp_path) == SHA


def test_detached_head(tmp_path):
    git = _repo(tmp_path)
    (git / "HEAD").write_text(SHA + "\n")
    assert run_git_sha(tmp_path) == SHA


def test_worktree_gitdir_file_and_commondir(tmp_path):
    main = _repo(tmp_path / "main")
    (main / "refs" / "heads" / "feature").write_text(OTHER + "\n")
    wt_git = main / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/feature\n")
    (wt_git / "commondir").write_text("../..\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_git}\n")
    assert run_git_sha(wt) == OTHER


def test_no_repository_is_none(tmp_path):
    assert run_git_sha(tmp_path) is None
