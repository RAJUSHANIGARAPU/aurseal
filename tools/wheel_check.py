"""The built aurseal wheel holds only the package.

``python -m tools.wheel_check dist`` exits 1 when the directory does not hold exactly one
aurseal wheel, or when that wheel carries a file outside ``aurseal/`` and its
``aurseal-<version>.dist-info/`` metadata (the framework, the demo system, local tooling
config). Shared by package.yml and publish.yml, so the wheel that is published passes the
same check as the wheel built on every change.
"""

from __future__ import annotations

import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

PREFIXES = ("aurseal/", "aurseal-")


def stray_entries(wheel: Path) -> list[str]:
    """Every entry of ``wheel`` outside the package and its metadata."""
    with zipfile.ZipFile(wheel) as archive:
        return [name for name in archive.namelist() if not name.startswith(PREFIXES)]


def check(directory: Path) -> list[str]:
    """Problems with the wheel in ``directory``; empty when it is clean."""
    wheels = sorted(directory.glob("aurseal-*.whl"))
    if len(wheels) != 1:
        return [f"expected one aurseal wheel in {directory}, found {len(wheels)}"]
    stray = stray_entries(wheels[0])
    if stray:
        return [f"files outside aurseal/ in {wheels[0].name}: {stray}"]
    return []


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    directory = Path(args[0] if args else "dist")
    problems = check(directory)
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print(f"{next(directory.glob('aurseal-*.whl')).name}: only the package")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
