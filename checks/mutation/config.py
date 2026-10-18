"""Which code is mutated, which tests must kill the mutants, and the score each must reach.

Read from a TOML file's ``[tool.wiki_checks.mutation]`` table (``checks/mutation/mutation.toml``
by default; the same table works inside a ``pyproject.toml``)::

    [tool.wiki_checks.mutation]
    max_children = 4
    timeout_s = 3600
    also_copy = ["schemas"]

    [tool.wiki_checks.mutation.packages.verification]
    source_paths = ["verification"]
    tests = ["tests/verification"]
    threshold = 0.70

``only_mutate`` (glob patterns) narrows a package to some files; ``deselect`` drops tests that
cannot run under mutmut. ``copy_repository = true`` copies every top-level entry (except the
mutated sources) next to the mutants, for tests that read repository files. A threshold is
required: a package without one could not fail.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEFAULT_CONFIG = Path(__file__).resolve().parent / "mutation.toml"
#: Pytest arguments for every mutant run: drop the repository's ``addopts`` (report
#: directories, browser channel) and the cache, so each mutant pays only for its tests.
DEFAULT_PYTEST_ARGS = ("-o", "addopts=", "-p", "no:cacheprovider", "-q")


@dataclass(frozen=True)
class PackageScope:
    name: str
    source_paths: tuple[str, ...]
    tests: tuple[str, ...]
    threshold: float
    only_mutate: tuple[str, ...] = ()
    #: Tests left out of the killing suite, each a pytest node id. Only for tests that cannot
    #: run under mutmut (they start a fresh Python process, which cannot load mutated code).
    deselect: tuple[str, ...] = ()


@dataclass(frozen=True)
class MutationConfig:
    packages: tuple[PackageScope, ...]
    max_children: int = 4
    timeout_s: int = 3600
    also_copy: tuple[str, ...] = ()
    copy_repository: bool = False
    pytest_args: tuple[str, ...] = DEFAULT_PYTEST_ARGS
    source: Path | None = field(default=None, compare=False)

    @property
    def thresholds(self) -> dict[str, float]:
        return {p.name: p.threshold for p in self.packages}

    def select(self, names: list[str] | None) -> tuple[PackageScope, ...]:
        if not names:
            return self.packages
        known = {p.name: p for p in self.packages}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(f"unknown package(s) {unknown}; configured: {sorted(known)}")
        return tuple(known[n] for n in names)

    @classmethod
    def from_pyproject(cls, path: Path | str = DEFAULT_CONFIG) -> MutationConfig:
        path = Path(path)
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"cannot read mutation config {path}: {exc}") from exc
        table = data.get("tool", {}).get("wiki_checks", {}).get("mutation")
        if not isinstance(table, dict):
            raise ValueError(f"{path} has no [tool.wiki_checks.mutation] table")
        return cls.from_table(table, source=path)

    @classmethod
    def from_table(cls, table: dict[str, Any], source: Path | None = None) -> MutationConfig:
        raw_packages = table.get("packages") or {}
        if not isinstance(raw_packages, dict) or not raw_packages:
            raise ValueError("no packages configured: nothing would be mutated")
        overrides = table.get("thresholds") or {}
        packages = []
        for name, body in raw_packages.items():
            if not isinstance(body, dict):
                raise ValueError(f"package {name!r} must be a table")
            threshold = overrides.get(name, body.get("threshold"))
            if not isinstance(threshold, int | float) or isinstance(threshold, bool):
                raise ValueError(f"package {name!r} has no numeric threshold: it could not fail")
            if not 0 <= float(threshold) <= 1:
                raise ValueError(f"package {name!r}: threshold must be within 0..1")
            sources = tuple(str(s) for s in body.get("source_paths") or ())
            tests = tuple(str(t) for t in body.get("tests") or ())
            if not sources or not tests:
                raise ValueError(f"package {name!r} needs source_paths and tests")
            packages.append(
                PackageScope(
                    name=str(name),
                    source_paths=sources,
                    tests=tests,
                    threshold=float(threshold),
                    only_mutate=tuple(str(g) for g in body.get("only_mutate") or ()),
                    deselect=tuple(str(d) for d in body.get("deselect") or ()),
                )
            )
        return cls(
            packages=tuple(packages),
            max_children=int(table.get("max_children", 4)),
            timeout_s=int(table.get("timeout_s", 3600)),
            also_copy=tuple(str(a) for a in table.get("also_copy") or ()),
            copy_repository=bool(table.get("copy_repository", False)),
            pytest_args=tuple(str(a) for a in table.get("pytest_args") or DEFAULT_PYTEST_ARGS),
            source=source,
        )
