"""Select the tests a code change can affect, from a committed area map. Opt-in.

Input is what a change touches: the changed file paths (``git diff --name-only``).
``config/test_areas.json`` maps each area to the source globs it owns, the tests that
exercise it and optional pytest markers. Selecting by the services a promotion moves is
``gate.service_selection``.

Every rule leans toward running more:

* a path in ``always_full`` (conftest, ``core/``, ``gate/``, ``config/``, the pytest and
  dependency files, workflows) selects the full suite: shared code reaches every test;
* a Python file every test loads (the root conftest's imports and their imports) selects
  the full suite; this set is computed from the import graph, not listed;
* a path no area claims selects the full suite: an unknown change runs everything;
* a changed test module selects itself; any other file under a test root (helpers,
  fixtures, data) selects the full suite, because who reads it is not declared;
* an empty change set selects the full suite unless ``--allow-empty`` says nothing
  changed on purpose.

The map is not trusted on its word. :func:`check_map` fails when an area names a glob,
test path or marker that does not exist, and when a test that imports an area's code
(directly, through other modules, or through a conftest above it) is missing from the
area's tests. So the map can over-select but cannot quietly under-select Python
dependencies. Non-Python files are invisible to that check, which is why area globs
name ``.py`` files and any other changed file falls through to the full suite.

Off by default. CI uses it only on a pull request with the repository variable
``SELECTIVE_TESTS=true``; a push to ``main`` always runs the full suite.
Why: docs/ci-decisions.md#selective-tests

``python -m gate.selective select --base B --head H`` prints the summary and the
pytest arguments; ``run`` executes the offline lane on the selection; ``check`` validates
the map. Exit 0 ok, 1 a ``check`` finding or a failing run, 2 bad input.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import json
import os
import shlex
import subprocess
import sys
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gate.change_scope import DiffUnavailable, changed_paths, matches_any, path_matches

ROOT = Path(__file__).resolve().parents[1]
MAP_FILE = ROOT / "config" / "test_areas.json"
_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__"})
_SKIP_TOP = frozenset({"reports", "ext-config"})  # run artifacts and a second checkout


def _skipped(rel: Path) -> bool:
    return bool(set(rel.parts) & _SKIP_DIRS) or (bool(rel.parts) and rel.parts[0] in _SKIP_TOP)


class MapError(ValueError):
    """The area map is malformed: exit 2."""


# ---------------------------------------------------------------------------
# the map
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Area:
    name: str
    paths: tuple[str, ...]
    tests: tuple[str, ...]
    markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class AreaMap:
    always_full: tuple[str, ...]
    areas: tuple[Area, ...]

    @classmethod
    def load(cls, path: str | Path = MAP_FILE) -> AreaMap:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MapError(f"cannot read {path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("areas"), dict):
            raise MapError(f"{path}: expected an object with an 'areas' object")
        areas = []
        for name, raw in data["areas"].items():
            if not isinstance(raw, dict) or not raw.get("tests"):
                raise MapError(f"{path}: area {name!r} needs a non-empty 'tests' list")
            if not raw.get("paths"):
                raise MapError(f"{path}: area {name!r} owns no paths")
            areas.append(
                Area(
                    name=name,
                    paths=tuple(raw.get("paths", ())),
                    tests=tuple(raw["tests"]),
                    markers=tuple(raw.get("markers", ())),
                )
            )
        return cls(always_full=tuple(data.get("always_full", ())), areas=tuple(areas))


def testpaths(root: str | Path = ROOT) -> tuple[str, ...]:
    """``testpaths`` from ``pytest.ini``: where test modules live."""
    parser = configparser.ConfigParser()
    parser.read(Path(root) / "pytest.ini")
    raw = parser.get("pytest", "testpaths", fallback="tests")
    return tuple(p.strip().rstrip("/") for p in raw.split() if p.strip())


def _under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def is_test_module(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


# ---------------------------------------------------------------------------
# the selection
# ---------------------------------------------------------------------------


@dataclass
class Selection:
    """What to run, and why each part of it was picked."""

    full_reasons: list[str] = field(default_factory=list)
    tests: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    markers: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    notes: list[str] = field(default_factory=list)

    @property
    def full(self) -> bool:
        return bool(self.full_reasons)

    @property
    def empty(self) -> bool:
        return not self.full and not self.tests and not self.markers

    def summary(self) -> str:
        if self.full:
            lines = ["Full suite:"] + [f"  - {r}" for r in self.full_reasons]
        elif self.empty:
            lines = ["Nothing selected."]
        else:
            lines = ["Selected:"]
            lines += [f"  - {t}: {'; '.join(r)}" for t, r in sorted(self.tests.items())]
            lines += [f"  - -m {m}: {'; '.join(r)}" for m, r in sorted(self.markers.items())]
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {
            "full": self.full,
            "full_reasons": self.full_reasons,
            "tests": {k: v for k, v in sorted(self.tests.items())},
            "markers": {k: v for k, v in sorted(self.markers.items())},
            "notes": self.notes,
        }

    def pytest_args(self, scope: Sequence[str] = (), base_marker: str = "") -> list[str] | None:
        """pytest arguments for one job: ``scope`` paths, its own ``-m``, and this selection.

        ``None`` means nothing in this job's scope was selected. A full selection returns
        the job's own arguments unchanged. Paths and markers together are a union pytest
        cannot express in one command line, so that case is resolved to node ids.
        """
        marker = f" and ({base_marker})" if base_marker else ""
        if self.full:
            return [*scope, *(["-m", base_marker] if base_marker else [])]
        paths = [t for t in sorted(self.tests) if not scope or any(_under(t, s) for s in scope)]
        if self.markers and paths:
            return None  # resolved by the caller: see resolve_node_ids
        if self.markers:
            expression = " or ".join(sorted(self.markers))
            return [*scope, "-m", f"({expression}){marker}"]
        if paths:
            return [*paths, *(["-m", base_marker] if base_marker else [])]
        return None


def _add_full(selection: Selection, reason: str) -> None:
    if reason not in selection.full_reasons:
        selection.full_reasons.append(reason)


def _add_area(selection: Selection, area: Area, why: str) -> None:
    for test in area.tests:
        if why not in selection.tests[test]:
            selection.tests[test].append(why)
    for marker in area.markers:
        if why not in selection.markers[marker]:
            selection.markers[marker].append(why)


def select_for_paths(
    paths: Iterable[str],
    area_map: AreaMap,
    root: str | Path = ROOT,
    *,
    allow_empty: bool = False,
    shared: Collection[str] | None = None,
) -> Selection:
    """Apply the path rules (module docstring) to a change set.

    ``shared`` is the set of files every test loads (:func:`shared_files`); computed from
    the repository when not given and a source module changed.
    """
    selection = Selection()
    changed = sorted({p.strip() for p in paths if p.strip()})
    if not changed:
        if allow_empty:
            selection.notes.append("empty change set, explicitly allowed")
        else:
            _add_full(selection, "empty change set (pass --allow-empty to select nothing)")
        return selection

    roots = testpaths(root)
    for path in changed:
        if matches_any(path, area_map.always_full):
            _add_full(selection, f"{path} is shared infrastructure")
            continue
        test_root = next((r for r in roots if _under(path, r)), None)
        if test_root is not None:
            if not is_test_module(path):
                _add_full(selection, f"{path} is a test helper or data file")
            elif (Path(root) / path).is_file():
                selection.tests[path].append("the test module itself changed")
            else:
                selection.notes.append(f"{path} was deleted; nothing to run for it")
            continue
        if path.endswith(".py"):
            if shared is None:
                shared = shared_files(root)
            if path in shared:
                _add_full(selection, f"{path} is loaded by every test (root conftest)")
                continue
        owners = [a for a in area_map.areas if matches_any(path, a.paths)]
        if not owners:
            _add_full(selection, f"{path} belongs to no area")
            continue
        for area in owners:
            _add_area(selection, area, f"area {area.name} ({path})")
    return selection


def resolve_node_ids(
    selection: Selection, scope: Sequence[str], base_marker: str, root: str | Path = ROOT
) -> list[str]:
    """Union of the selected paths and markers, as node ids pytest itself collected."""
    from core.ci_selection import collect_node_ids

    marker = f" and ({base_marker})" if base_marker else ""
    paths = [t for t in sorted(selection.tests) if not scope or any(_under(t, s) for s in scope)]
    ids: set[str] = set()
    if paths:
        ids |= collect_node_ids([*paths, *(["-m", base_marker] if base_marker else [])], root)
    if selection.markers:
        expression = " or ".join(sorted(selection.markers))
        ids |= collect_node_ids([*scope, "-m", f"({expression}){marker}"], root)
    return sorted(ids)


# ---------------------------------------------------------------------------
# checking the map against the repository
# ---------------------------------------------------------------------------


def _python_files(root: Path) -> dict[str, str]:
    """Dotted module name -> repository-relative path, for every module in the repo."""
    modules: dict[str, str] = {}
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if _skipped(rel):
            continue
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and all(p.isidentifier() for p in parts):
            modules[".".join(parts)] = rel.as_posix()
    return modules


def _imports(path: Path, module: str, is_package: bool, known: Mapping[str, str]) -> set[str]:
    """Modules a file loads: imports (any depth, lazy ones too), their parents, plugins."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return set()
    package = module if is_package else module.rpartition(".")[0]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".") if package else []
                anchor = anchor[: len(anchor) - (node.level - 1)] if node.level > 1 else anchor
                base = ".".join([*anchor, base] if base else anchor)
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # pytest_plugins = ["pkg.plugin"], importlib.import_module("pkg.mod")
            if node.value in known and "." in node.value:
                names.add(node.value)
    found: set[str] = set()
    for name in names:
        parts = name.split(".")
        for cut in range(1, len(parts) + 1):
            candidate = ".".join(parts[:cut])
            if candidate in known:
                found.add(candidate)
    found.discard(module)
    return found


def dependency_index(root: str | Path = ROOT) -> dict[str, set[str]]:
    """Test module path -> every repository file it loads, conftests above it included."""
    root = Path(root)
    modules = _python_files(root)
    edges = {
        name: _imports(root / rel, name, rel.endswith("__init__.py"), modules)
        for name, rel in modules.items()
    }

    closure_cache: dict[str, frozenset[str]] = {}

    def closure(start: str) -> frozenset[str]:
        if start in closure_cache:
            return closure_cache[start]
        seen, stack = {start}, [start]
        while stack:
            for nxt in edges.get(stack.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        closure_cache[start] = frozenset(seen)
        return closure_cache[start]

    by_path = {rel: name for name, rel in modules.items()}
    index: dict[str, set[str]] = {}
    roots = testpaths(root)
    for rel, name in by_path.items():
        if not is_test_module(rel) or not any(_under(rel, r) for r in roots):
            continue
        starts = [name]
        parent = Path(rel).parent
        while True:
            conftest = (parent / "conftest.py").as_posix().removeprefix("./")
            if conftest in by_path:
                starts.append(by_path[conftest])
            if parent == Path("."):
                break
            parent = parent.parent
        loaded: set[str] = set()
        for start in starts:
            loaded |= closure(start)
        index[rel] = {modules[m] for m in loaded}
    return index


def shared_files(root: str | Path = ROOT) -> frozenset[str]:
    """Files every test module loads: a change to one of them reaches the whole suite."""
    index = dependency_index(root)
    return frozenset(set.intersection(*index.values())) if index else frozenset()


def _declared_markers(root: Path) -> set[str]:
    parser = configparser.ConfigParser()
    parser.read(root / "pytest.ini")
    lines = parser.get("pytest", "markers", fallback="").splitlines()
    return {line.split(":", 1)[0].split("(", 1)[0].strip() for line in lines if line.strip()}


def _tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=str(root), capture_output=True, text=True, check=False
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.splitlines()
    return [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not _skipped(p.relative_to(root))
    ]


def check_map(area_map: AreaMap, root: str | Path = ROOT) -> list[str]:
    """Problems with the map: dead references, and tests an area would fail to select."""
    root = Path(root)
    problems: list[str] = []
    files = _tracked_files(root)
    markers = _declared_markers(root)
    for pattern in area_map.always_full:
        if not any(path_matches(f, pattern) for f in files):
            problems.append(f"always_full glob {pattern!r} matches no file")
    for area in area_map.areas:
        for pattern in area.paths:
            if not any(path_matches(f, pattern) for f in files):
                problems.append(f"area {area.name}: glob {pattern!r} matches no file")
        for test in area.tests:
            if not (root / test).exists():
                problems.append(f"area {area.name}: test path {test} does not exist")
        for marker in area.markers:
            if marker not in markers:
                problems.append(f"area {area.name}: marker {marker!r} is not declared")

    index = dependency_index(root)
    shared = set.intersection(*index.values()) if index else set()
    for area in area_map.areas:
        # A file every test loads selects the full suite before any area is consulted.
        owned = {
            f for f in files if f.endswith(".py") and f not in shared and matches_any(f, area.paths)
        }
        for test, loaded in sorted(index.items()):
            hits = sorted(loaded & owned)
            if hits and not any(_under(test, t) for t in area.tests):
                problems.append(
                    f"area {area.name}: {test} loads {hits[0]}"
                    + (f" (+{len(hits) - 1} more)" if len(hits) > 1 else "")
                    + " but is not in the area's tests"
                )
    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# The offline lane in .github/workflows/tests.yml; `run` narrows exactly this command.
OFFLINE_SCOPE = ("tests/",)
OFFLINE_MARKER = "not demo_ui and not draft and not render_check"


def _selection_from_args(args: argparse.Namespace, area_map: AreaMap) -> Selection:
    if args.paths is not None:
        paths = args.paths
    else:
        try:
            paths = changed_paths(args.base or "", args.head or "", args.root)
        except DiffUnavailable as exc:
            selection = Selection()
            _add_full(selection, str(exc))
            return selection
    return select_for_paths(paths, area_map, args.root, allow_empty=args.allow_empty)


def _write(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="python -m gate.selective")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("select", "run"):
        p = sub.add_parser(name)
        p.add_argument("--base", default=env.get("CHANGE_BASE"), help="diff base ref")
        p.add_argument("--head", default=env.get("CHANGE_HEAD") or "HEAD", help="diff head ref")
        p.add_argument("--paths", nargs="*", help="changed paths instead of a git diff")
        p.add_argument("--allow-empty", action="store_true", help="no change selects nothing")
        p.add_argument("--map", default=str(MAP_FILE))
        p.add_argument("--root", default=str(ROOT))
        p.add_argument("--summary", default=env.get("GITHUB_STEP_SUMMARY"))
    sub.choices["select"].add_argument("--format", choices=("text", "json", "args"), default="text")
    sub.choices["run"].add_argument("--dry-run", action="store_true")
    chk = sub.add_parser("check", help="validate the area map against the repository")
    chk.add_argument("--map", default=str(MAP_FILE))
    chk.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)

    try:
        area_map = AreaMap.load(args.map)
    except MapError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.command == "check":
        problems = check_map(area_map, args.root)
        for problem in problems:
            print(problem)
        print(f"{len(problems)} problem(s) in {args.map}")
        return 1 if problems else 0

    try:
        selection = _selection_from_args(args, area_map)
    except Exception as exc:  # a bad manifest is bad input, not a crash
        print(f"cannot work out the change: {exc}", file=sys.stderr)
        return 2

    if args.command == "select":
        if args.format == "json":
            print(json.dumps(selection.to_json(), indent=2))
            return 0
        extra = selection.pytest_args()
        if extra is None and not selection.empty:
            extra = resolve_node_ids(selection, (), "", args.root)
        if args.format == "args":
            print("\n".join(extra or []))
            return 0
        print(selection.summary())
        if selection.full:
            print("pytest arguments: none (the lane's own full command)")
        elif extra is not None and len(extra) > 20:
            print(f"pytest arguments: {len(extra)} node ids (--format args lists them)")
        elif extra is not None:
            print("pytest arguments: " + shlex.join(extra))
        return 0

    # run: the offline lane, narrowed to the selection.
    _write(args.summary, "### Selective offline run\n```\n" + selection.summary() + "\n```")
    print(selection.summary())
    if selection.empty:
        print("nothing selected; no pytest run")
        return 0
    full_lane = [*OFFLINE_SCOPE, "-m", OFFLINE_MARKER]
    if selection.full:
        lane_args = full_lane
    else:
        # Resolved to node ids up front: the job's own marker can deselect every chosen
        # path, and that must not surface as pytest's exit 5. A selection with nothing in
        # this lane runs the whole lane rather than nothing.
        lane_args = resolve_node_ids(selection, OFFLINE_SCOPE, OFFLINE_MARKER, args.root)
        if not lane_args:
            print("nothing selected is in the offline lane; running the whole lane")
            lane_args = full_lane
    command = [sys.executable, "-m", "pytest", *lane_args, "-n", "auto"]
    shown = command if len(lane_args) <= 20 else [*command[:3], f"<{len(lane_args)} node ids>"]
    print(shlex.join(shown))
    if args.dry_run:
        return 0
    return subprocess.run(command, cwd=args.root, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
