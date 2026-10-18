"""One leak-gate run: load the lists, scan the sources, decide the exit code.

Exit codes: 0 clean, 1 findings, 2 cannot run (missing/empty/invalid list under
``missing_list="fail"``, bad allowlist, unreadable source), 3 not assessed (``fork``: the
denylist is unavailable by design; personal-data rules still ran and found nothing).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from testkeel.leakgate import allow as allowlist
from testkeel.leakgate import denylist as deny
from testkeel.leakgate.scan import Finding, Scanner

CLEAN, FINDINGS, CANNOT_RUN, NOT_ASSESSED = 0, 1, 2, 3


@dataclass
class Outcome:
    code: int
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None


def run(
    trees: Sequence[Path] = (),
    messages: Sequence[tuple[str, Path]] = (),
    commit_range: str | None = None,
    history: bool = False,
    range_root: Path = Path("."),
    denylist_path: Path | None = None,
    allow_path: Path | None = None,
    missing_list: str = "fail",
    fork: bool = False,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Outcome:
    env = dict(os.environ if environ is None else environ)
    notes: list[str] = []
    try:
        lists = None if fork else deny.load(denylist_path, env, home)
    except deny.DenylistError as exc:
        return Outcome(CANNOT_RUN, error=str(exc))
    if fork:
        notes.append("NOT ASSESSED: denylist not available (fork or bot pull request)")
    elif not lists:
        what = "empty" if lists is not None else "missing"
        if missing_list != "warn":
            return Outcome(
                CANNOT_RUN,
                error=f"the denylist is {what}; set ${deny.ENV_VAR} or create "
                f"~/{deny.CONFIG_PATH.as_posix()}",
            )
        notes.append(f"warning: the denylist is {what}; only the built-in rules ran")
        lists = None
    try:
        allows = allowlist.load(allow_path) if allow_path else ()
        if allow_path is None:
            allows = allowlist.load((trees[0] if trees else range_root) / allowlist.FILE_NAME)
    except (allowlist.AllowError, OSError) as exc:
        return Outcome(CANNOT_RUN, error=str(exc))

    scanner = Scanner(lists, allows, env.get("HOME"))
    try:
        for tree in trees:
            if not tree.is_dir():
                return Outcome(CANNOT_RUN, error=f"{tree}: not a directory")
            scanner.tree(tree)
        for label, path in messages:
            scanner.message(label, path.read_text(encoding="utf-8", errors="replace"))
        if commit_range:
            scanner.commit_range(range_root, commit_range)
        if history:
            scanner.history(range_root)
    except (OSError, RuntimeError) as exc:
        return Outcome(CANNOT_RUN, error=str(exc))

    findings = sorted(scanner.findings)
    notes.extend(scanner.notes)
    if findings:
        return Outcome(FINDINGS, findings, notes)
    return Outcome(NOT_ASSESSED if fork else CLEAN, [], notes)
