"""Review state for generated tests: ``draft`` until a human approves it in a reviewed PR.

Every generator writes a module-level marker into the files it emits::

    pytestmark = pytest.mark.draft(generator="spec", source="specs/x.md",
                                   generated_at="2026-01-01T00:00:00Z")

Approval is removing that marker in a reviewed pull request: git history is the approval
record (a CODEOWNERS entry on generated paths makes the review mandatory where branch
protection is on). Rejecting is deleting the file.

What the state changes:

* ``draft`` is excluded from the default gate (``gate.lanes``): a draft can neither
  redden nor green it. Drafts still run, in their own non-required CI job.
* Draft results never count as evidence: the matrix reports ``DRAFT_ONLY``, plans have
  ``no_draft_evidence``, the release verdict ignores them.
* ``lint_generated`` fails a file under a generator output path that has neither the
  marker nor a commit in its history that removed one (a generator that forgot to mark).
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from traceability._util import utc_now

MARKER = "draft"
PATHS_ENV = "GENERATED_TEST_PATHS"
DEFAULT_PATHS = ("generated_tests",)
_DRAFT_CALL = "pytest.mark.draft("
_DRAFT_RE = re.compile(r"pytest\.mark\.draft\(")

ReviewState = Literal["draft", "approved"]


def draft_marker_source(generator: str, source: str, generated_at: str | None = None) -> str:
    """The ``pytest.mark.draft(...)`` expression a generator writes (one line)."""
    stamp = generated_at or utc_now()
    return f"pytest.mark.draft(generator={generator!r}, source={source!r}, generated_at={stamp!r})"


def is_draft_source(text: str) -> bool:
    return bool(_DRAFT_RE.search(text))


NOTE = "# Generated test awaiting review: remove the draft marker in a reviewed PR to approve."


def _marker_lines(generator: str, source: str, generated_at: str | None, indent: str) -> list[str]:
    stamp = generated_at or utc_now()
    return [
        f"{indent}pytest.mark.draft(",
        f"{indent}    generator={generator!r},",
        f"{indent}    source={source!r},",
        f"{indent}    generated_at={stamp!r},",
        f"{indent})",
    ]


def mark_draft(code: str, generator: str, source: str, generated_at: str | None = None) -> str:
    """``code`` with the draft marker added (idempotent; ``import pytest`` added if missing).

    The marker block starts with :data:`NOTE` and ends with a ``)`` or ``]`` line at column
    0, so :func:`unmark_draft` can remove exactly what was added. Code that does not parse
    is returned unchanged: the caller's own syntax check reports it.
    """
    if is_draft_source(code):
        return code
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    has_pytest = any(
        isinstance(node, ast.Import) and any(a.name == "pytest" for a in node.names)
        for node in tree.body
    )
    has_pytestmark = any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
        for node in tree.body
    )
    lines = code.splitlines()
    if has_pytestmark:  # keep the module's own marks, add ours after them
        block = [
            NOTE,
            "pytestmark = [",
            "    *(pytestmark if isinstance(pytestmark, list) else [pytestmark]),",
            *_marker_lines(generator, source, generated_at, "    ")[:-1],
            "    ),",
            "]",
        ]
        return "\n".join([*lines, "", *block]) + "\n"
    anchor = 0  # after the leading docstring and imports
    for index, node in enumerate(tree.body):
        docstring = (
            index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        )
        if not (docstring or isinstance(node, (ast.Import, ast.ImportFrom))):
            break
        anchor = node.end_lineno or node.lineno
    marker = _marker_lines(generator, source, generated_at, "")
    marker[0] = "pytestmark = " + marker[0]
    block = [NOTE, *([] if has_pytest else ["import pytest"]), *marker]
    block = ["", *block] if anchor else [*block, ""]
    return "\n".join(lines[:anchor] + block + lines[anchor:]) + "\n"


def unmark_draft(code: str) -> str:
    """Remove a block :func:`mark_draft` added (what a reviewer does to approve)."""
    lines = code.splitlines()
    if NOTE not in lines:
        return code
    start = lines.index(NOTE)
    end = start
    while end < len(lines) and lines[end] not in (")", "]"):
        end += 1
    if start > 0 and lines[start - 1] == "":
        start -= 1
    elif end + 1 < len(lines) and lines[end + 1] == "":
        end += 1
    return "\n".join(lines[:start] + lines[end + 1 :]) + "\n"


def review_state(item) -> ReviewState:
    """``draft`` when the item (function, class or module) carries the marker."""
    return "draft" if item.get_closest_marker(MARKER) is not None else "approved"


def draft_details(item) -> dict[str, str]:
    mark = item.get_closest_marker(MARKER)
    if mark is None:
        return {}
    return {k: str(v) for k, v in mark.kwargs.items() if k in ("generator", "source")}


# ---------------------------------------------------------------------- lint


@dataclass(frozen=True)
class LintFinding:
    path: str
    problem: str

    def __str__(self) -> str:
        return f"{self.path}: {self.problem}"


def generated_paths(env: dict[str, str] | None = None) -> list[Path]:
    raw = (env if env is not None else os.environ).get(PATHS_ENV, "")
    parts = [p for p in raw.split(os.pathsep) if p.strip()]
    return [Path(p) for p in (parts or DEFAULT_PATHS)]


def _test_files(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for base in paths:
        if base.is_file():
            out.append(base)
        elif base.is_dir():
            out += sorted(
                p
                for p in base.rglob("*.py")
                if p.name.startswith("test_") or p.name.endswith("_test.py")
            )
    return out


def marker_removed_in_history(path: Path, cwd: Path) -> bool | None:
    """True when a commit removed the draft marker from ``path``; None when git cannot tell."""
    try:
        proc = subprocess.run(
            ["git", "log", "--format=%H", "-p", "-S", _DRAFT_CALL, "--", str(path)],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return any(
        line.startswith("-") and not line.startswith("---") and _DRAFT_CALL in line
        for line in proc.stdout.splitlines()
    )


def lint_generated(paths: Sequence[Path], *, cwd: Path | None = None) -> list[LintFinding]:
    """Findings for generated test files that are neither draft nor approved by a removal."""
    root = cwd or Path.cwd()
    findings = []
    for path in _test_files(paths):
        if is_draft_source(path.read_text(encoding="utf-8", errors="replace")):
            continue
        removed = marker_removed_in_history(path, root)
        if removed is None:
            findings.append(
                LintFinding(str(path), "no draft marker, and git cannot show an approving removal")
            )
        elif not removed:
            findings.append(
                LintFinding(
                    str(path),
                    "generated test without a draft marker and no reviewed commit removing one",
                )
            )
    return findings
