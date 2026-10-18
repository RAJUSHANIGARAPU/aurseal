"""What a check scans: the staged diff, a commit range, or the whole tree.

Every source yields ``ScanFile`` objects: a repo-relative path, its full text
and the set of line numbers that are new in the change (``None`` = every line
counts, as in a full-tree scan). Range scans also yield the commit messages.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# Lockfiles are machine-written; the scratch gate skipped them too.
SKIP_SUFFIXES = (".lock",)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class ScanFile:
    path: str
    text: str
    added: frozenset[int] | None  # None: the whole file is in scope


@dataclass(frozen=True)
class CommitMessage:
    sha: str
    text: str


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )
    return out.stdout.decode("utf-8", errors="replace")


def _git_bytes(repo: Path, *args: str) -> bytes | None:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    return out.stdout if out.returncode == 0 else None


def _decode(blob: bytes | None) -> str | None:
    if blob is None or b"\0" in blob[:8192]:
        return None  # missing or binary
    return blob.decode("utf-8", errors="replace")


def parse_added_lines(diff: str) -> dict[str, set[int]]:
    """Map each path in a ``git diff -U0`` to the new-side line numbers it adds."""
    added: dict[str, set[int]] = {}
    path: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            path = None if target == "/dev/null" else target.removeprefix("b/")
            if path is not None:
                added.setdefault(path, set())
            continue
        m = _HUNK.match(line)
        if m and path is not None:
            start = int(m.group(1))
            count = 1 if m.group(2) is None else int(m.group(2))
            added[path].update(range(start, start + count))
    return added


def _skip(path: str) -> bool:
    return path.endswith(SKIP_SUFFIXES)


def staged(repo: Path) -> Iterator[ScanFile]:
    """Files in the index that differ from HEAD, with their staged content."""
    diff = _git(repo, "diff", "--cached", "-U0", "--no-color", "--no-ext-diff")
    for path, lines in sorted(parse_added_lines(diff).items()):
        if _skip(path) or not lines:
            continue
        text = _decode(_git_bytes(repo, "show", f":{path}"))
        if text is not None:
            yield ScanFile(path, text, frozenset(lines))


def commit_range(repo: Path, rev_range: str) -> Iterator[ScanFile]:
    """Files changed across ``rev_range`` (``BASE..HEAD``), content at its tip."""
    tip = rev_range.split("..")[-1] or "HEAD"
    diff = _git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", rev_range)
    for path, lines in sorted(parse_added_lines(diff).items()):
        if _skip(path) or not lines:
            continue
        text = _decode(_git_bytes(repo, "show", f"{tip}:{path}"))
        if text is not None:
            yield ScanFile(path, text, frozenset(lines))


def range_messages(repo: Path, rev_range: str) -> Iterator[CommitMessage]:
    out = _git(repo, "log", "--format=%H%x00%B%x01", rev_range)
    for record in out.split("\x01"):
        record = record.strip("\n")
        if record:
            sha, _, body = record.partition("\x00")
            yield CommitMessage(sha[:12], body)


def tree(repo: Path) -> Iterator[ScanFile]:
    """Every tracked file plus untracked, non-ignored ones, as on disk."""
    listing = _git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    for path in sorted(set(filter(None, listing.split("\0")))):
        full = repo / path
        if _skip(path) or not full.is_file():
            continue
        text = _decode(full.read_bytes())
        if text is not None:
            yield ScanFile(path, text, None)


def repo_root(start: Path) -> Path:
    return Path(_git(start, "rev-parse", "--show-toplevel").strip())
