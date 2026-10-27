"""Is this change documentation only? Decides whether CI may skip the browser jobs.

A push or pull request that touches nothing but documentation and tooling notes cannot
change what a browser test observes, so the browser and live-site jobs have nothing to
say about it. The offline suite still runs: it checks the docs against the code
(``tests/test_docs_drift.py``), so a docs change is not untested, only not browsed.

The rule is deliberately one-sided. Anything this module cannot prove is docs-only runs
everything:

* a path outside :data:`DOCS_ONLY_PATTERNS` (the single list to maintain);
* no known change set: a scheduled or manual run, the first push of a branch (no
  ``before``), an empty diff, a diff git could not compute.

The workflow skips jobs with a job-level ``if:`` on this module's output, never with
``paths-ignore``: a skipped job still reports, so a required check does not hang.

``python -m gate.change_scope classify`` reads ``CHANGE_EVENT``, ``CHANGE_BASE`` and
``CHANGE_HEAD`` (or ``--paths``), writes ``docs_only=true|false`` to ``$GITHUB_OUTPUT``
and one line to ``$GITHUB_STEP_SUMMARY``. Exit 0 with a verdict, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import functools
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Paths a change may touch and still skip the browser jobs. Top-level Markdown only:
# a Markdown file inside a package can be read by code (specs, templates, plans).
# Why: docs/ci-decisions.md#docs-only-changes
DOCS_ONLY_PATTERNS: tuple[str, ...] = ("docs/**", "*.md", ".claude/**")

# Events whose diff this module can compute. Everything else has no change set.
DIFF_EVENTS = frozenset({"push", "pull_request"})
_NO_COMMIT = re.compile(r"^0+$")


@functools.lru_cache(maxsize=512)
def _pattern_re(pattern: str) -> re.Pattern[str]:
    """Translate a path glob: ``**`` spans directories, ``*`` and ``?`` stay inside one."""
    out = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out) + r"\Z")


def path_matches(path: str, pattern: str) -> bool:
    """Whether a repository-relative POSIX path matches a glob (``**`` = any depth)."""
    path = path.strip()
    path = path[2:] if path.startswith("./") else path
    return _pattern_re(pattern).match(path) is not None


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(path_matches(path, pattern) for pattern in patterns)


@dataclass(frozen=True)
class Verdict:
    docs_only: bool
    reason: str
    other_paths: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        head = "docs-only change: browser jobs skipped" if self.docs_only else "full run"
        return f"{head} ({self.reason})"


def classify(paths: Iterable[str], patterns: Sequence[str] = DOCS_ONLY_PATTERNS) -> Verdict:
    """Docs-only when there is at least one changed path and every one matches a pattern."""
    changed = sorted({p.strip() for p in paths if p.strip()})
    if not changed:
        return Verdict(False, "no changed paths known")
    other = tuple(p for p in changed if not matches_any(p, patterns))
    if other:
        shown = ", ".join(other[:3]) + (f" and {len(other) - 3} more" if len(other) > 3 else "")
        return Verdict(False, f"{len(other)} path(s) outside the docs list: {shown}", other)
    return Verdict(True, f"{len(changed)} path(s), all documentation")


class DiffUnavailable(RuntimeError):
    """The change set cannot be computed; the caller runs everything."""


def changed_paths(base: str, head: str, root: str | Path = ".") -> list[str]:
    """``git diff --name-only base...head``: what the change itself touched."""
    if not base or _NO_COMMIT.match(base):
        raise DiffUnavailable("no base commit (first push of the branch)")
    if not head:
        raise DiffUnavailable("no head commit")
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DiffUnavailable(f"git diff failed: {result.stderr.strip()[:200]}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def classify_event(env: Mapping[str, str], root: str | Path = ".") -> Verdict:
    """The verdict for a CI event described by ``CHANGE_EVENT/BASE/HEAD``."""
    event = env.get("CHANGE_EVENT", "")
    if event not in DIFF_EVENTS:
        return Verdict(False, f"event {event or 'unknown'!r} has no change set")
    try:
        paths = changed_paths(env.get("CHANGE_BASE", ""), env.get("CHANGE_HEAD", ""), root)
    except DiffUnavailable as exc:
        return Verdict(False, str(exc))
    return classify(paths)


def _append(path: str | None, line: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="python -m gate.change_scope")
    sub = parser.add_subparsers(dest="command", required=True)
    cls = sub.add_parser("classify", help="docs_only=true|false for this change")
    cls.add_argument("--paths", nargs="*", help="changed paths (default: diff from CHANGE_*)")
    cls.add_argument("--root", default=".")
    cls.add_argument("--github-output", default=env.get("GITHUB_OUTPUT"))
    cls.add_argument("--summary", default=env.get("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args(argv)

    verdict = classify(args.paths) if args.paths is not None else classify_event(env, args.root)
    print(verdict.summary())
    _append(args.github_output, f"docs_only={'true' if verdict.docs_only else 'false'}")
    _append(args.summary, f"Change scope: {verdict.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
