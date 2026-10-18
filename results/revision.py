"""Case versioning: a revision hash per test function, stamped on every result row.

* ``revision_id`` is git's blob hash (``sha1("blob <len>\\0" + text)``) of the test
  function's own normalised source (``inspect.getsource`` — decorators included —
  dedented, CRLF to LF, trailing whitespace stripped). ``git hash-object --stdin`` on the
  same text reproduces it, without shelling out to git.
* ``file_blob_sha`` is the blob hash of the whole test file as it is on disk (exactly
  ``git hash-object <file>``), which catches module-level fixture and helper changes.
* ``git_sha`` is the run's commit, read from ``GITHUB_SHA`` or from ``.git`` by plain file
  reads (no subprocess: a CI container without git still works).

What a trend can then tell apart: outcome changed with the revision and file unchanged
means the product or the environment changed; a changed revision means the test changed.
Not covered: fixtures in ``conftest.py``, page objects and helpers in other files. A
shared fixture change therefore looks like a product change unless the ``git_sha`` is
inspected; the history pages say so rather than claiming certainty.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

from results._util import loads
from results.models import DEFAULT_PROJECT, CaseRecord, RevisionRecord

_SHA = frozenset("0123456789abcdef")


def blob_sha(text: str | bytes) -> str:
    """git's blob id for ``text``: ``sha1(b"blob <len>\\0" + bytes)``."""
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def normalise_source(source: str) -> str:
    """Dedent, CRLF/CR to LF, trailing whitespace and surrounding blank lines removed."""
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    text = textwrap.dedent(text)
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def function_revision(func: Callable[..., Any]) -> str:
    """The revision hash of one test function (raises OSError/TypeError without source)."""
    return blob_sha(normalise_source(inspect.getsource(inspect.unwrap(func))))


def file_revision(path: Path) -> str:
    """``git hash-object <path>``: the blob hash of the file's bytes as they are on disk."""
    return blob_sha(Path(path).read_bytes())


def _is_sha(text: str) -> bool:
    return len(text) == 40 and set(text) <= _SHA


def _git_dir(rootdir: Path) -> Path | None:
    for base in (rootdir, *rootdir.parents):
        dot = base / ".git"
        if dot.is_dir():
            return dot
        if dot.is_file():  # a worktree or submodule: "gitdir: <path>"
            content = dot.read_text(encoding="utf-8").strip()
            if content.startswith("gitdir:"):
                target = Path(content[len("gitdir:") :].strip())
                return target if target.is_absolute() else (base / target).resolve()
    return None


def _resolve_ref(git_dir: Path, ref: str) -> str | None:
    common = git_dir
    commondir = git_dir / "commondir"
    if commondir.is_file():
        rel = commondir.read_text(encoding="utf-8").strip()
        common = (git_dir / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
    for base in (git_dir, common):
        loose = base / ref
        if loose.is_file():
            value = loose.read_text(encoding="utf-8").strip()
            if _is_sha(value):
                return value
    packed = common / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split(" ", 1)
            if len(parts) == 2 and parts[1] == ref and _is_sha(parts[0]):
                return parts[0]
    return None


def run_git_sha(rootdir: Path | str = ".") -> str | None:
    """The run's commit: ``GITHUB_SHA``, else ``.git/HEAD`` resolved by file reads; else None."""
    env = os.environ.get("GITHUB_SHA", "").strip()
    if env:
        return env
    try:
        git_dir = _git_dir(Path(rootdir).resolve())
        if git_dir is None:
            return None
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        try:
            return _resolve_ref(git_dir, head[len("ref:") :].strip())
        except OSError:
            return None
    return head if _is_sha(head) else None


def revision_from_item(
    item: Any, case: CaseRecord, *, git_sha: str | None = None
) -> RevisionRecord | None:
    """The revision of ``item``'s test function; None when its source is unavailable."""
    func = getattr(item, "function", None)
    if func is None:
        return None
    try:
        rev = function_revision(func)
    except (OSError, TypeError):
        return None
    file_sha = None
    try:
        file_sha = file_revision(Path(str(item.path)))
    except (OSError, AttributeError):
        pass
    return RevisionRecord(
        revision_id=rev,
        case_id=case.case_id,
        project_id=case.project_id,
        source_hash=rev,
        file_blob_sha=file_sha,
        git_sha=git_sha,
        steps=case.steps,
    )


def revisions_for_case(
    store: Any, case_id: str, *, project_id: str = DEFAULT_PROJECT
) -> list[RevisionRecord]:
    """Every stored revision of a case, oldest first."""
    rows = store.query("case_revisions_list", case_id=case_id, project_id=project_id)
    out = []
    for row in rows:
        data = dict(row)
        data["steps"] = tuple(loads(data.pop("steps_json", None), []) or ())
        out.append(RevisionRecord.from_json(data))
    return out


def revision_ranges(
    store: Any, case_id: str, *, project_id: str = DEFAULT_PROJECT
) -> list[dict[str, Any]]:
    """Revisions with the run range each one covered (first/last run, run count)."""
    out = []
    for row in store.query("case_revisions_list", case_id=case_id, project_id=project_id):
        data = dict(row)
        data["steps"] = loads(data.pop("steps_json", None), []) or []
        data.pop("id", None)
        out.append(data)
    return out


__all__ = [
    "blob_sha",
    "file_revision",
    "function_revision",
    "normalise_source",
    "revision_from_item",
    "revision_ranges",
    "revisions_for_case",
    "run_git_sha",
]
