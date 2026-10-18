"""Blocklist checker on synthetic names: never real ones."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.leak_gate import blocklist, gitsrc

TERMS = "# synthetic\nacmecorp\n\\bwidgetron\\b\nzz-\\d+\n"


@pytest.fixture
def blocklist_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "blocklist.txt"
    path.write_text(TERMS, encoding="utf-8")
    monkeypatch.setenv(blocklist.ENV_VAR, str(path))
    return path


def test_load_skips_comments_and_ignores_case(blocklist_file: Path) -> None:
    patterns = blocklist.load(blocklist_file)
    assert len(patterns) == 3
    assert blocklist.scan_text("we use ACMECORP here\nclean\nzz-42", patterns) == [
        (1, 1),
        (3, 3),
    ]


def test_word_boundary_entries_respect_boundaries(blocklist_file: Path) -> None:
    patterns = blocklist.load(blocklist_file)
    assert blocklist.scan_text("widgetronics", patterns) == []
    assert blocklist.scan_text("a widgetron b", patterns) == [(1, 2)]


@pytest.mark.parametrize("content", [None, "# only comments\n\n"])
def test_missing_or_empty_blocklist_fails_closed(
    tmp_path: Path, content: str | None, repo, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    path = tmp_path / "absent.txt"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setenv(blocklist.ENV_VAR, str(path))
    assert blocklist.main(["--staged", "--repo", str(repo.root)]) == 2
    assert "CANNOT RUN" in capsys.readouterr().err


def test_invalid_regex_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text("ok\n(unclosed\n", encoding="utf-8")
    with pytest.raises(blocklist.BlocklistUnavailable, match=":2:"):
        blocklist.load(path)


def test_default_path_is_outside_the_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(blocklist.ENV_VAR, raising=False)
    assert blocklist.blocklist_path() == Path("~/.config/leak-gate/blocklist.txt").expanduser()


def test_staged_reports_only_added_lines_and_never_the_text(
    repo, blocklist_file: Path, capsys
) -> None:
    repo.write("notes.txt", "old acmecorp line\n")
    repo.commit("seed")  # history is not the staged check's business
    repo.write("notes.txt", "old acmecorp line\nnew widgetron line\nclean\n")
    repo.git("add", "notes.txt")
    assert blocklist.main(["--staged", "--repo", str(repo.root)]) == 1
    out = capsys.readouterr().out
    assert "notes.txt:2: blocklist entry #2" in out
    assert "notes.txt:1" not in out
    assert "widgetron" not in out


def test_blocklisted_path_is_a_finding(repo, blocklist_file: Path, capsys) -> None:
    repo.write("docs/acmecorp-notes.md", "nothing here\n")
    repo.git("add", "-A")
    assert blocklist.main(["--staged", "--repo", str(repo.root)]) == 1
    assert "(path): blocklist entry #1" in capsys.readouterr().out


def test_range_covers_commit_messages(repo, blocklist_file: Path, capsys) -> None:
    base = repo.git("rev-parse", "HEAD").strip()
    repo.write("a.txt", "fine\n")
    sha = repo.commit("port from zz-7 work")
    assert blocklist.main(["--range", f"{base}..HEAD", "--repo", str(repo.root)]) == 1
    assert f"commit {sha[:12]} message line 1: blocklist entry #3" in capsys.readouterr().out


def test_range_clean(repo, blocklist_file: Path, capsys) -> None:
    base = repo.git("rev-parse", "HEAD").strip()
    repo.write("a.txt", "fine\n")
    repo.commit("plain change")
    assert blocklist.main(["--range", f"{base}..HEAD", "--repo", str(repo.root)]) == 0
    assert "clean" in capsys.readouterr().out


def test_tree_scans_tracked_and_untracked(repo, blocklist_file: Path, capsys) -> None:
    repo.write("tracked.txt", "acmecorp\n")
    repo.commit("t")
    repo.write("untracked.txt", "x\nwidgetron\n")
    assert blocklist.main(["--tree", "--repo", str(repo.root)]) == 1
    out = capsys.readouterr().out
    assert "tracked.txt:1:" in out and "untracked.txt:2:" in out


def test_message_file_ignores_git_comment_lines(tmp_path: Path, blocklist_file: Path) -> None:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("fix the thing\n# acmecorp in a git hint line\n", encoding="utf-8")
    assert blocklist.main(["--message-file", str(msg)]) == 0
    msg.write_text("fix the acmecorp thing\n", encoding="utf-8")
    assert blocklist.main(["--message-file", str(msg)]) == 1


def test_lockfiles_and_binaries_are_skipped(repo, blocklist_file: Path) -> None:
    repo.write("deps.lock", "acmecorp\n")
    (repo.root / "blob.bin").write_bytes(b"\0acmecorp")
    repo.git("add", "-A")
    assert list(gitsrc.staged(repo.root)) == []


def test_parse_added_lines_handles_hunks_and_deletions() -> None:
    diff = (
        "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,0 +2,3 @@\n+a\n+b\n+c\n"
        "@@ -9 +11 @@\n-o\n+n\n"
        "diff --git a/gone b/gone\n--- a/gone\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
    )
    assert gitsrc.parse_added_lines(diff) == {"x": {2, 3, 4, 11}}
