"""The scanner: sources (tree, commit range, message files) through the rules.

Findings carry a location and rule ids only, never the matched text. A path component that
itself hits a rule is masked in every location (``docs/ (name #2)``), so a content finding
inside such a file cannot re-publish the name either.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from testkeel.leakgate import allow as allowlist
from testkeel.leakgate.denylist import Denylist, Matcher
from testkeel.leakgate.normalise import line_tokens
from testkeel.leakgate.pii import HOME, line_rules
from testkeel.leakgate.project import ProjectRules

MAX_FILE_BYTES = 5 * 1024 * 1024
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache"}


@dataclass(frozen=True, order=True)
class Finding:
    location: str
    line: int | None
    rule: str
    entry: str | None = None

    def __str__(self) -> str:
        where = self.location if self.line is None else f"{self.location}:{self.line}"
        return f"{where} {self.rule}" + (f" [{self.entry}]" if self.entry else "")


@dataclass
class Scanner:
    denylist: Denylist | None
    allows: Sequence[allowlist.Allow] = ()
    home: str | None = None
    project: ProjectRules | None = None  # generated-project rules; None for a repository
    findings: set[Finding] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._matcher = Matcher(self.denylist) if self.denylist else None

    # ------------------------------------------------------------------ text

    def text(
        self,
        label: str,
        text: str,
        path: str | None = None,
        mask: str | None = None,
        pii: bool = True,
    ) -> None:
        """Scan one text. ``path`` is matched by the allowlist; ``mask`` is what is printed."""
        shown = mask or label
        lines = text.splitlines()
        if self._matcher is not None:
            for number, entry in self._matcher.tokens(line_tokens(text)):
                self.findings.add(Finding(shown, number, entry.rule, entry.entry_id))
            for number, entry in self._matcher.lines(text):
                self.findings.add(Finding(shown, number, entry.rule, entry.entry_id))
        if not pii:
            return
        check = line_rules(path or label, self.home)
        for number, line in enumerate(lines, start=1):
            for rule in check(line):
                if not any(a.covers(rule, path or label, line) for a in self.allows):
                    self.findings.add(Finding(shown, number, rule))

    # ------------------------------------------------------------------ names

    def names(self, paths: Iterable[str]) -> dict[str, str]:
        """Check every path component; return each path's printable (masked) form.

        A hit is reported as ``<parent>/ (name #k)``: the k-th name, in sorted order, among
        the scanned names in that directory. The name itself is never printed.
        """
        unique = sorted(set(paths))
        children: dict[tuple[str, ...], set[str]] = {}
        for path in unique:
            parts = PurePosixPath(path).parts
            for depth, part in enumerate(parts):
                children.setdefault(parts[:depth], set()).add(part)
        masked: dict[tuple[str, ...], str] = {}
        for parent in sorted(children, key=len):
            for k, name in enumerate(sorted(children[parent]), start=1):
                shown_parent = masked.get(parent, "/".join(parent))
                hits = self._name_hits(name)
                if hits:
                    where = f"{shown_parent}/ (name #{k})" if parent else f"./ (name #{k})"
                    for rule, entry in hits:
                        self.findings.add(Finding(where, None, rule, entry))
                    shown = f"(name #{k})"
                else:
                    shown = name
                masked[(*parent, name)] = f"{shown_parent}/{shown}" if parent else shown
        return {path: masked[PurePosixPath(path).parts] for path in unique}

    def _name_hits(self, name: str) -> list[tuple[str, str | None]]:
        hits: list[tuple[str, str | None]] = []
        if self._matcher is not None:
            for _line, entry in self._matcher.tokens(line_tokens(name)):
                hits.append((entry.rule, entry.entry_id))
            for _line, entry in self._matcher.lines(name):
                hits.append((entry.rule, entry.entry_id))
        return hits

    # ------------------------------------------------------------------ sources

    def tree(self, root: Path) -> None:
        files = list_files(root)
        shown = self.names(files)
        skipped = 0
        for relative in files:
            path = root / relative
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    skipped += 1
                    self._project_path(relative, shown[relative], None)
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            binary = b"\0" in data[:8192]
            text = None if binary else data.decode("utf-8", errors="replace")
            self._project_path(relative, shown[relative], text)
            if text is None:
                continue  # binary: its name was checked above
            self.text(relative, text, relative, shown[relative])
            if self.project is not None:
                for number, rule in self.project.content(relative, text):
                    self.findings.add(Finding(shown[relative], number, rule))
        if skipped:
            self.notes.append(
                f"{skipped} file(s) over {MAX_FILE_BYTES // 1024 // 1024} MB not read"
            )

    def _project_path(self, relative: str, shown: str, text: str | None) -> None:
        if self.project is None:
            return
        parts = shown.split("/")
        for depth, rule in self.project.path(relative, text):
            where = "/".join(parts[:depth]) + ("/" if depth < len(parts) else "")
            self.findings.add(Finding(where, None, rule))

    def message(self, label: str, text: str) -> None:
        self.text(label, text)

    def commit_range(self, root: Path, spec: str) -> None:
        """Commit messages, added lines and changed file names of ``spec`` (``A..B``)."""
        log = _git(root, "log", "--format=%H%x00%B%x1e", spec)
        for chunk in log.split("\x1e"):
            sha, _, body = chunk.strip("\n").partition("\x00")
            if sha:
                self.text(f"commit {sha[:7]} msg", body)
        diff = _git(
            root, "-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff",
            "--unified=0", spec,
        )  # fmt: skip
        added: dict[str, list[tuple[int, str]]] = {}
        for path, number, line in _added_lines(diff.splitlines()):
            added.setdefault(path, []).append((number, line))
        self._added(added, self.names(added), "")

    def history(self, root: Path) -> None:
        """Every commit message and added line of every ref, against the denylist only.

        A newly listed term must be checked against everything already published. The
        personal-data rules are not applied here: published history cannot be fixed without
        a rewrite, and a check that stays red for that reason gets ignored. They run on the
        tree, on pull-request diffs and on messages instead.
        """
        commits: list[tuple[str, dict[str, list[tuple[int, str]]]]] = []
        lines = _git(
            root, "-c", "core.quotepath=off", "log", "--all", "--no-color", "--no-ext-diff",
            "--unified=0", "-p", "--format=%x1e%H%x00%B%x00",
        ).split("\x1e")  # fmt: skip
        for chunk in lines:
            sha, _, rest = chunk.lstrip("\n").partition("\x00")
            if not sha:
                continue
            body, _, patch = rest.partition("\x00")
            self.text(f"commit {sha[:7]} msg", body, pii=False)
            added: dict[str, list[tuple[int, str]]] = {}
            for path, number, line in _added_lines(patch.splitlines()):
                added.setdefault(path, []).append((number, line))
            commits.append((sha, added))
        shown = self.names({path for _, added in commits for path in added})
        for sha, added in commits:
            self._added(added, shown, f"commit {sha[:7]} ", pii=False)

    def _added(
        self,
        added: dict[str, list[tuple[int, str]]],
        shown: dict[str, str],
        prefix: str,
        pii: bool = True,
    ) -> None:
        for path, lines in added.items():
            for number, line in lines:
                probe = Scanner(self.denylist, self.allows, self.home)
                probe.text(path, line, path, prefix + shown[path], pii=pii)
                for finding in probe.findings:
                    self.findings.add(
                        Finding(finding.location, number, finding.rule, finding.entry)
                    )


def list_files(root: Path) -> list[str]:
    """Files that would be committed: git's view when ``root`` is a work tree, else a walk."""
    if (root / ".git").exists():
        try:
            out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
            return sorted(p for p in out.split("\0") if p and (root / p).is_file())
        except RuntimeError:
            pass
    found = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in files:
            found.append((Path(directory) / name).relative_to(root).as_posix())
    return sorted(found)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, errors="replace"
    )
    if result.returncode != 0:
        # git's stderr can quote a ref or path; report the command shape only.
        raise RuntimeError(f"git {args[0]} failed with exit {result.returncode}")
    return result.stdout


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _added_lines(diff: Iterable[str]) -> Iterator[tuple[str, int, str]]:
    path: str | None = None
    number = 0
    for line in diff:
        if line.startswith("+++ "):
            target = line[4:]
            path = None if target == "/dev/null" else target.removeprefix("b/")
        elif line.startswith("@@"):
            match = _HUNK_RE.match(line)
            number = int(match.group(1)) if match else 0
        elif line.startswith("+") and path is not None:
            yield path, number, line[1:]
            number += 1


__all__ = ["HOME", "Finding", "Scanner", "list_files"]
