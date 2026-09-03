"""Bounded discovery of a project's own Python files.

``source_dir`` defaults to ``"."`` in every tier-3 plugin, and a plain
``rglob("*.py")`` from there walks the virtualenv with it: measured in this repo
at 3573 files, 3353 of them under ``site-packages``, roughly 26 seconds to parse.
``BasePlugin.execute`` then retries three times with no executor timeout, so a
plugin whose only real work is reading a dozen project modules could hold a run
for minutes and report on dependencies nobody asked about.

Pruning matters more than filtering. ``os.walk`` is used rather than ``rglob``
so an excluded directory is never descended into at all — filtering the results
of a full walk still pays for the walk.

The cap is the second half of the same problem: a directory that is not on the
exclusion list but is still enormous (a vendored tree, a data dump of ``.py``
fixtures) would otherwise reintroduce it. Callers are told when the cap bit, so
"I looked at everything" and "I looked at the first N" stay distinguishable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Directory names never descended into. Environments and caches, not sources.
EXCLUDED_DIR_NAMES = frozenset(
    {
        ".bzr",
        ".eggs",
        ".git",
        ".hg",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        "__pycache__",
        "ai_generated_tests",
        "build",
        "dist",
        "env",
        "generated_tests",
        "node_modules",
        "reports",
        "site-packages",
        "venv",
    }
)

#: Ceiling on files returned. Well above any plausible project source tree and
#: well below the 3573 a virtualenv contributes, so it bounds the pathological
#: case without truncating the normal one.
DEFAULT_FILE_LIMIT = 2000


@dataclass(frozen=True)
class SourceScan:
    """Files found, and whether the search was complete."""

    files: list[Path]
    truncated: bool

    def __bool__(self) -> bool:
        return bool(self.files)


def _is_excluded(name: str) -> bool:
    # `.venv`, `.direnv`, `.virtualenv` and friends all start with a dot and end
    # up holding an interpreter; matching the prefix is cheaper and more durable
    # than enumerating every naming fashion. Egg-info is suffix-matched for the
    # same reason.
    return name in EXCLUDED_DIR_NAMES or name.startswith(".venv") or name.endswith(".egg-info")


def iter_source_files(root: Path, limit: int = DEFAULT_FILE_LIMIT) -> SourceScan:
    """Return the project's own ``*.py`` files under ``root``, deterministically.

    A missing or non-directory ``root`` yields no files rather than raising:
    the caller has to decide what an empty tree means for its verdict, and for
    every tier-3 plugin that answer is ``unknown``, not ``pass``.
    """
    if not root.is_dir():
        return SourceScan(files=[], truncated=False)

    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Mutated in place — this is what stops os.walk descending.
        dirnames[:] = sorted(d for d in dirnames if not _is_excluded(d))
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            found.append(Path(dirpath) / filename)
            if len(found) >= limit:
                return SourceScan(files=found, truncated=True)
    return SourceScan(files=found, truncated=False)
