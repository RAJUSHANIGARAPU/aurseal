"""Fail when a change contains a blocklisted name or term.

The blocklist is a plain-text file OUTSIDE this repository: one
case-insensitive Python regex per line, ``#`` starts a comment line. Default
``~/.config/leak-gate/blocklist.txt``, overridden by ``LEAK_GATE_BLOCKLIST``.
A missing or empty blocklist fails closed (exit 2): an unmeasured change is
not a clean one.

Findings report ``path:line`` and the blocklist entry NUMBER, never the
matched text or the pattern, so a CI log does not republish the names the
gate exists to keep out.

Modes::

    python -m tools.leak_gate.blocklist --staged              # pre-commit
    python -m tools.leak_gate.blocklist --range BASE..HEAD    # CI, incl. messages
    python -m tools.leak_gate.blocklist --tree                # whole tree
    python -m tools.leak_gate.blocklist --message-file FILE   # commit-msg hook

Exit status: 0 clean; 1 findings; 2 blocklist missing, empty or invalid.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.leak_gate import gitsrc

ENV_VAR = "LEAK_GATE_BLOCKLIST"
DEFAULT_PATH = Path("~/.config/leak-gate/blocklist.txt")


class BlocklistUnavailable(Exception):
    """The blocklist cannot be loaded; the gate must not pass."""


@dataclass(frozen=True)
class Finding:
    where: str  # "path:line" or "commit <sha> message line N"
    entry: int  # 1-based index into the blocklist's entries


def blocklist_path() -> Path:
    return Path(os.environ.get(ENV_VAR) or DEFAULT_PATH).expanduser()


def load(path: Path) -> list[re.Pattern[str]]:
    if not path.is_file():
        raise BlocklistUnavailable(
            f"blocklist not found at {path}. Put it there (one regex per line) or set "
            f"{ENV_VAR}. The gate fails closed without it."
        )
    patterns: list[re.Pattern[str]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line, re.IGNORECASE))
        except re.error as exc:
            raise BlocklistUnavailable(f"{path}:{number}: invalid regex ({exc})") from exc
    if not patterns:
        raise BlocklistUnavailable(f"blocklist {path} has no entries; refusing to pass")
    return patterns


def scan_text(
    text: str, patterns: Sequence[re.Pattern[str]], lines: Iterable[int] | None = None
) -> list[tuple[int, int]]:
    """(line, entry) for every blocklisted line; ``lines`` limits the scan."""
    wanted = None if lines is None else set(lines)
    hits: list[tuple[int, int]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if wanted is not None and number not in wanted:
            continue
        for entry, pattern in enumerate(patterns, 1):
            if pattern.search(line):
                hits.append((number, entry))
                break
    return hits


def scan_files(
    files: Iterable[gitsrc.ScanFile], patterns: Sequence[re.Pattern[str]]
) -> list[Finding]:
    findings: list[Finding] = []
    for f in files:
        # A blocklisted file NAME leaks as surely as its content.
        for entry, pattern in enumerate(patterns, 1):
            if pattern.search(f.path):
                findings.append(Finding(f"{f.path} (path)", entry))
                break
        for line, entry in scan_text(f.text, patterns, f.added):
            findings.append(Finding(f"{f.path}:{line}", entry))
    return findings


def scan_messages(
    messages: Iterable[gitsrc.CommitMessage], patterns: Sequence[re.Pattern[str]]
) -> list[Finding]:
    return [
        Finding(f"commit {m.sha} message line {line}", entry)
        for m in messages
        for line, entry in scan_text(m.text, patterns)
    ]


def _message_body(text: str) -> str:
    # git strips '#' lines from the message before it is stored; so do we.
    return "\n".join("" if ln.startswith("#") else ln for ln in text.splitlines())


def run(args: argparse.Namespace) -> int:
    try:
        patterns = load(blocklist_path())
    except BlocklistUnavailable as exc:
        print(f"LEAK GATE (blocklist): CANNOT RUN: {exc}", file=sys.stderr)
        return 2
    repo = gitsrc.repo_root(Path(args.repo))
    if args.staged:
        findings = scan_files(gitsrc.staged(repo), patterns)
    elif args.range:
        findings = scan_files(gitsrc.commit_range(repo, args.range), patterns)
        findings += scan_messages(gitsrc.range_messages(repo, args.range), patterns)
    elif args.tree:
        findings = scan_files(gitsrc.tree(repo), patterns)
    else:
        text = _message_body(Path(args.message_file).read_text(encoding="utf-8"))
        findings = [
            Finding(f"commit message line {line}", entry)
            for line, entry in scan_text(text, patterns)
        ]
    for f in findings:
        print(f"{f.where}: blocklist entry #{f.entry}")
    if findings:
        print(
            f"LEAK GATE (blocklist): FAIL, {len(findings)} finding(s). Rename or re-derive "
            "with invented values; the entry number points into your local blocklist.",
            file=sys.stderr,
        )
        return 1
    print("LEAK GATE (blocklist): clean")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="added lines in the index")
    mode.add_argument("--range", metavar="BASE..HEAD", help="added lines + commit messages")
    mode.add_argument("--tree", action="store_true", help="every tracked/untracked file")
    mode.add_argument("--message-file", metavar="FILE", help="a commit message file")
    parser.add_argument("--repo", default=".", help="repository to scan (default: cwd)")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
