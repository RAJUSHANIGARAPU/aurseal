"""
Walking a source tree without walking its dependencies.

Three tier-4 plugins opened every ``Path(source_dir).rglob("*.py")``, and
``source_dir`` defaults to ``"."``. In this checkout that is 3573 files, 3353 of
them under ``.venv/.../site-packages`` — roughly twenty-six seconds of reading
and parsing other people's code, and ``llm_output_oracle`` does it on a
BACKGROUND daemon thread that nobody joins and nothing times out.

The findings were worse than the cost. ``behavioral_equivalence`` snapshotted the
public functions of every installed package, so upgrading a dependency read as a
refactor of this repository.

``os.walk`` rather than ``rglob`` because the pruning has to happen on the way
down: by the time ``rglob`` yields a path it has already descended into the tree
that path came from.

The file cap is not a performance guard, it is an honesty one. A scan that
stopped early has not seen the tree it is about to report on, so it says
``truncated`` and lets the caller downgrade its verdict rather than present a
sample as the whole.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Directory names never worth descending into. Dependencies, caches and build
#: output are not this repository's source and must not appear in its findings.
#: ``reports`` and the two generated-test trees are excluded for a second
#: reason: they hold what these plugins themselves write, and a plugin that
#: reads its own output back reports on itself.
_EXCLUDED_DIRS = frozenset(
    {
        "site-packages",
        "node_modules",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "reports",
        "ai_generated_tests",
        "generated_tests",
    }
)

#: Well past the ~220 source files this repository holds, so a truncated scan
#: means something unexpected is under ``source_dir`` — which is precisely when
#: the caller should stop claiming to have looked at all of it.
MAX_FILES = 5000


@dataclass(frozen=True)
class SourceScan:
    """What a walk found, and whether it saw everything."""

    root: Path
    files: list[Path]
    truncated: bool

    @property
    def count(self) -> int:
        return len(self.files)


def scan_source_files(
    root: Path | str,
    *,
    limit: int = MAX_FILES,
    skip_tests: bool = True,
) -> SourceScan:
    """Collect ``*.py`` under ``root``, pruning dependency and cache trees.

    Sorted at every level, so two walks over an unchanged tree yield the same
    files in the same order. A snapshot diff is only meaningful against a stable
    ordering, and ``os.walk`` does not promise one.
    """
    root_path = Path(root)
    collected: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Assigning into the slice is what prunes the walk; rebinding the name
        # would leave os.walk holding the original list.
        dirnames[:] = sorted(
            d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")
        )
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            if skip_tests and "test" in filename:
                continue
            if len(collected) >= limit:
                return SourceScan(root=root_path, files=collected, truncated=True)
            collected.append(Path(dirpath) / filename)
    return SourceScan(root=root_path, files=collected, truncated=False)


def relative_key(path: Path, root: Path | str) -> str:
    """A stable, comparable name for a file inside ``root``.

    Absolute paths carry the checkout directory, which differs between the
    snapshot run and the run that reads it back — so a diff keyed on them sees
    every file as removed and every file as added.
    """
    try:
        return path.relative_to(Path(root)).as_posix()
    except ValueError:
        return path.as_posix()
