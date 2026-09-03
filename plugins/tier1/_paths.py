"""
Where tier-1 plugins look, and where they refuse to look.

Every tier-1 path was resolved against the process working directory. Nothing
recorded which directory that had been, so the same run scanned a different
tree depending on where it was launched from, and ``e2e_playwright`` reporting
"the directory does not exist" was indistinguishable from "you started me one
level up". Defaults are now anchored to the repository root; an absolute path
in the context still wins, because callers and tests legitimately point the
plugins somewhere else.

``iter_source_files`` additionally refuses to walk a virtualenv. ``source_dir``
defaulted to ``"."`` and ``rglob("*.py")`` over this checkout returns 3,574
files, 3,353 of them under ``.venv/lib/.../site-packages`` — third-party code
nobody asked about, ``ast.parse``d on every run at a measured 25.9s, and then
repeated by ``BasePlugin.execute``'s three retries.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

#: Repository root, from this file's own location — the one anchor that does
#: not move when the process is started from somewhere unexpected.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Directory names never worth parsing: dependencies, caches, build output, and
#: the plugins' own generated tests — which would otherwise be picked up as
#: input on the next run and generate tests for themselves.
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "node_modules",
        "site-packages",
        "build",
        "dist",
        "reports",
        "ai_generated_tests",
    }
)


def resolve_dir(context: dict, key: str, default: str) -> Path:
    """Resolve a directory from ``context[key]``, anchored to the repo root.

    A relative path — the default included — is taken against ``REPO_ROOT``
    rather than the working directory, so the answer does not depend on where
    the process happened to start. An absolute path is used as given.
    """
    raw = context.get(key)
    path = Path(raw) if raw is not None else Path(default)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def iter_source_files(root: Path) -> Iterator[Path]:
    """Yield every ``*.py`` under ``root``, pruning dependency and build trees.

    Pruning during the walk rather than filtering afterwards: on a checkout
    with a virtualenv the skipped subtree is 94% of the files.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield Path(dirpath) / name
