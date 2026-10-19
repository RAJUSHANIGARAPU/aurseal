"""Drive mutmut 3.x and read its results.

mutmut 3 is configured only through ``[tool.mutmut]`` in the working directory's
``pyproject.toml`` (or ``setup.cfg``); its CLI has no ``--paths-to-mutate`` any more. So each
package runs in its own copy of the repository (``reports/mutation/work/<package>``) with a
generated ``[tool.mutmut]`` table: the repository's own files are never touched and the
mutants directory never lands in the checkout.

Per package:

1. ``python -m mutmut run --max-children N``
2. ``python -m mutmut results --all true`` — one ``<mutant>: <status>`` line per mutant, the
   structured listing the counts are taken from;
3. ``python -m mutmut export-cicd-stats`` — mutmut's own totals, used to cross-check.

Verified against mutmut 3.8.0. Any step failing, a timeout, unparseable output, or totals that
disagree is "no answer", not a score.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from checks.mutation.config import MutationConfig, PackageScope

#: mutmut 3 status names (``mutmut.stats.status_by_exit_code``).
STATUSES = (
    "killed",
    "survived",
    "no tests",
    "timeout",
    "suspicious",
    "skipped",
    "not checked",
    "caught by type check",
    "segfault",
    "check was interrupted by user",
)
_LINE = re.compile(
    r"^\s*(?P<name>[^\s:][^:]*?__mutmut_\d+): (?P<status>" + "|".join(STATUSES) + r")\s*$"
)
_COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "reports", "mutants", "__pycache__", "node_modules", "*.pyc"
)


#: Never copied next to the mutants. ``__init__.py``: a repository whose root is a package
#: would make pytest import the *unmutated* copy one level up, so no test ever reaches a
#: mutant (this repository's root is not one; the guard is for any repository scanned).
#: ``conftest.py``: the root conftest imports the framework's own plugins
#: (and the packages under test) before mutmut's per-test switching applies. ``tests`` and the
#: config files mutmut copies itself.
_NEVER_COPY = frozenset(
    {"mutants", "pyproject.toml", "setup.cfg", "tests", "__init__.py", "conftest.py"}
)


class MutmutUnavailable(RuntimeError):
    """mutmut is not installed."""


@dataclass
class MutationCounts:
    killed: int = 0
    survived: int = 0
    timeout: int = 0
    no_tests: int = 0
    suspicious: int = 0
    skipped: int = 0
    not_checked: int = 0
    caught_by_type_check: int = 0
    segfault: int = 0
    interrupted: int = 0
    survivors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.killed
            + self.survived
            + self.timeout
            + self.no_tests
            + self.suspicious
            + self.skipped
            + self.not_checked
            + self.caught_by_type_check
            + self.segfault
            + self.interrupted
        )


@dataclass
class MutmutRun:
    package: str
    ran: bool
    reason: str = ""
    counts: MutationCounts | None = None
    stats: dict | None = None
    tool_version: str | None = None
    output_tail: str = ""


def mutmut_version() -> str | None:
    if importlib.util.find_spec("mutmut") is None:
        return None
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("mutmut")
    except PackageNotFoundError:  # pragma: no cover
        return "unknown"


def parse_results(text: str) -> MutationCounts:
    """Counts from ``mutmut results --all true``. Lines that are not a mutant line (progress
    bars, emoji summaries, warnings) are ignored rather than mined for numbers."""
    counts = MutationCounts()
    attr = {
        "killed": "killed",
        "survived": "survived",
        "no tests": "no_tests",
        "timeout": "timeout",
        "suspicious": "suspicious",
        "skipped": "skipped",
        "not checked": "not_checked",
        "caught by type check": "caught_by_type_check",
        "segfault": "segfault",
        "check was interrupted by user": "interrupted",
    }
    for line in text.splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        status = match.group("status")
        setattr(counts, attr[status], getattr(counts, attr[status]) + 1)
        if status in ("survived", "no tests"):
            counts.survivors.append(match.group("name"))
    return counts


def _toml_list(values: tuple[str, ...] | list[str]) -> str:
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


def _also_copy(scope: PackageScope, config: MutationConfig, work: Path | None) -> list[str]:
    """Extra files next to the mutants; never a mutated source path (it would overwrite it)."""
    names = list(config.also_copy)
    if config.copy_repository and work is not None:
        names += sorted(p.name for p in work.iterdir() if p.name not in _NEVER_COPY)
    sources = {s.strip("/").split("/")[0] for s in scope.source_paths}
    return list(dict.fromkeys(n for n in names if n.strip("/").split("/")[0] not in sources))


def mutmut_table(scope: PackageScope, config: MutationConfig, work: Path | None = None) -> str:
    args = [*config.pytest_args, *(arg for d in scope.deselect for arg in ("--deselect", d))]
    lines = [
        "",
        "[tool.mutmut]",
        f"source_paths = {_toml_list(scope.source_paths)}",
        f"pytest_add_cli_args = {_toml_list(args)}",
        f"pytest_add_cli_args_test_selection = {_toml_list(scope.tests)}",
    ]
    if scope.only_mutate:
        lines.append(f"only_mutate = {_toml_list(scope.only_mutate)}")
    also = _also_copy(scope, config, work)
    if also:
        lines.append(f"also_copy = {_toml_list(also)}")
    return "\n".join(lines) + "\n"


def prepare_workdir(repo: Path, work: Path, scope: PackageScope, config: MutationConfig) -> Path:
    """A fresh copy of the repository with ``[tool.mutmut]`` for this package."""
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(repo, work, ignore=_COPY_IGNORE, symlinks=True)
    pyproject = work / "pyproject.toml"
    existing = pyproject.read_text(encoding="utf-8") if pyproject.is_file() else ""
    if "[tool.mutmut]" in existing:
        raise ValueError("the repository's pyproject.toml already has [tool.mutmut]")
    pyproject.write_text(existing + mutmut_table(scope, config, work), encoding="utf-8")
    return work


def _call(args: list[str], work: Path, timeout_s: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mutmut", *args],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def run(config: MutationConfig, scope: PackageScope, *, repo: Path, work_root: Path) -> MutmutRun:
    version = mutmut_version()
    if version is None:
        raise MutmutUnavailable("mutmut is not installed (pip install -r requirements-checks.txt)")
    # Not named after the package: if the copied root were a package, pytest would import
    # the copy under its directory name, and a copy called ``checks`` would shadow the real
    # ``checks`` package.
    work = prepare_workdir(repo, work_root / f"mutmut-{scope.name}", scope, config)
    try:
        proc = _call(["run", "--max-children", str(config.max_children)], work, config.timeout_s)
    except subprocess.TimeoutExpired:
        return MutmutRun(scope.name, False, f"mutmut did not finish within {config.timeout_s}s")
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-800:]
    if proc.returncode != 0:
        return MutmutRun(
            scope.name,
            False,
            f"mutmut run exited {proc.returncode} (the clean test run or stats collection "
            f"failed): {tail[-300:]}",
            tool_version=version,
            output_tail=tail,
        )
    try:
        results = _call(["results", "--all", "true"], work, 300)
        _call(["export-cicd-stats"], work, 300)
        stats = json.loads((work / "mutants" / "mutmut-cicd-stats.json").read_text("utf-8"))
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        return MutmutRun(
            scope.name, False, f"mutmut results unreadable: {exc}", tool_version=version
        )
    counts = parse_results(results.stdout or "")
    if not isinstance(stats, dict) or stats.get("total") != counts.total:
        return MutmutRun(
            scope.name,
            False,
            f"mutmut's totals ({stats.get('total') if isinstance(stats, dict) else stats}) "
            f"disagree with its listing ({counts.total})",
            tool_version=version,
        )
    return MutmutRun(scope.name, True, "", counts, stats, version, tail)
