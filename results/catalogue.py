"""Test-case catalogue generated from code.

Every collected pytest item becomes a :class:`~results.models.CaseRecord`:

* **Identity** is function level: ``case_key`` is ``<path>::<Class>.<function>``, so the
  50 parameter rows of one test are one case with 50 ``param_id`` values. An optional
  ``@pytest.mark.case_id("TC-0042")`` pins the identity across renames and moves;
  without it a rename is a removed case plus a new one (the catalogue diff shows it).
* **Fields**: title (allure title, else first docstring line, else the humanised name),
  description (rest of the docstring), steps (a ``Steps:`` block in the docstring, else
  the literal ``with allure.step("…")`` strings found by a static AST walk — the test is
  never run; a computed name shows as ``<dynamic>``), owner (``owner`` marker, else the
  ``CODEOWNERS`` rule that matches the file), priority (``priority`` marker, else the
  allure severity), markers (every name; arguments for the ones that carry meaning).
* **Links**: ``requirement``, ``feature`` (and allure ``feature`` labels) and
  ``known_defect`` markers become ``case_links`` rows, so traceability and living docs
  read one place.
* **Removal is only decided on a full collection** (``python -m results catalogue
  build`` with no selection arguments and no collection errors); a filtered run only
  refreshes ``last_seen_at``.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from results._util import utc_now
from results.models import DEFAULT_PROJECT, CaseRecord, RevisionRecord

CASE_ID_MARKER = "case_id"
OWNER_MARKER = "owner"
PRIORITY_MARKER = "priority"
FEATURE_MARKER = "feature"
REQUIREMENT_MARKER = "requirement"
DEFECT_MARKER = "known_defect"
ALLURE_LABEL_MARKER = "allure_label"
DYNAMIC_STEP = "<dynamic>"
CATALOGUE_SCHEMA = "catalogue-v1.schema.json"

# Markers whose arguments are worth keeping in the catalogue.
ARG_MARKERS = frozenset(
    {
        REQUIREMENT_MARKER,
        FEATURE_MARKER,
        DEFECT_MARKER,
        CASE_ID_MARKER,
        OWNER_MARKER,
        PRIORITY_MARKER,
        "journey_group",
    }
)

MARKER_LINES = (
    "case_id(id): pin a test case's identity across renames (results catalogue)",
    "owner(team): who owns the test case (results catalogue)",
    "priority(level): test case priority, e.g. P1 (results catalogue)",
    "feature(name): the product feature a test case covers (catalogue, living docs)",
)

_STEPS_HEADER = re.compile(r"^\s*steps\s*:\s*$", re.IGNORECASE)
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.+?)\s*$")


# ---------------------------------------------------------------------- identity


def case_key_from_nodeid(nodeid: str) -> str:
    """``tests/x.py::TestA::test_b[1]`` -> ``tests/x.py::TestA.test_b``."""
    head = nodeid
    if head.endswith("]") and "[" in head:
        head = head[: head.index("[")]
    path, sep, rest = head.partition("::")
    if not sep:
        return path
    return f"{path}::{rest.replace('::', '.')}"


def case_id_for_key(case_key: str) -> str:
    """The derived (unpinned) id of a case key: stable as long as the key is."""
    return "c-" + hashlib.sha1(case_key.encode("utf-8")).hexdigest()[:16]


def case_id_for_nodeid(nodeid: str) -> str:
    """The case id a pytest node id maps to when the test carries no ``case_id`` pin."""
    return case_id_for_key(case_key_from_nodeid(nodeid))


# ---------------------------------------------------------------------- fields


def _humanise(name: str) -> str:
    name = re.sub(r"^test_?", "", name)
    words = name.replace("_", " ").strip()
    return (words[:1].upper() + words[1:]) if words else name


def split_docstring(doc: str | None) -> tuple[str | None, str | None]:
    """(first line, the rest) of a cleaned docstring."""
    if not doc:
        return None, None
    text = inspect.cleandoc(doc)
    first, _, rest = text.partition("\n")
    rest = rest.strip()
    return (first.strip() or None), (rest or None)


def _docstring_steps(doc: str | None) -> list[str]:
    if not doc:
        return []
    lines = inspect.cleandoc(doc).splitlines()
    steps: list[str] = []
    in_block = False
    for line in lines:
        if not in_block:
            in_block = bool(_STEPS_HEADER.match(line))
            continue
        if not line.strip():
            if steps:
                break
            continue
        match = _NUMBERED.match(line)
        if not match:
            break
        steps.append(match.group(2))
    return steps


def _is_step_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "step":
        return isinstance(func.value, ast.Name) and func.value.id == "allure"
    return isinstance(func, ast.Name) and func.id == "step"


def _ast_steps(func: Callable[..., Any]) -> list[str]:
    try:
        source = textwrap.dedent(inspect.getsource(func))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError, IndentationError):
        return []
    found: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            call = item.context_expr
            if not _is_step_call(call):
                continue
            arg = call.args[0] if call.args else None  # type: ignore[attr-defined]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                text = arg.value
            else:
                text = DYNAMIC_STEP
            found.append((call.lineno, call.col_offset, text))
    return [text for _, _, text in sorted(found)]


def extract_steps(func: Callable[..., Any] | None, docstring: str | None) -> list[str]:
    """Explicit ``Steps:`` block first, else literal ``allure.step`` strings (static only)."""
    steps = _docstring_steps(docstring)
    if steps:
        return steps
    return _ast_steps(func) if func is not None else []


@dataclass(frozen=True)
class _OwnerRule:
    pattern: str
    owners: tuple[str, ...]


def _codeowners_file(rootdir: Path) -> Path | None:
    for candidate in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        path = rootdir / candidate
        if path.is_file():
            return path
    return None


_owner_cache: dict[Path, list[_OwnerRule]] = {}


def codeowners_rules(rootdir: Path) -> list[_OwnerRule]:
    rootdir = Path(rootdir).resolve()
    if rootdir in _owner_cache:
        return _owner_cache[rootdir]
    rules: list[_OwnerRule] = []
    path = _codeowners_file(rootdir)
    if path is not None:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            rules.append(_OwnerRule(parts[0], tuple(parts[1:])))
    _owner_cache[rootdir] = rules
    return rules


def _owner_match(pattern: str, rel: str) -> bool:
    anchored = pattern.startswith("/")
    pat = pattern.lstrip("/")
    if pat.endswith("/"):
        pat += "**"
    if pat == "*":
        return True
    if "/" not in pat.rstrip("*").rstrip("/") and not anchored:
        # A bare name matches at any depth.
        return any(fnmatch(part, pat) for part in rel.split("/")) or fnmatch(rel, pat)
    pat = pat.replace("**", "*")
    return fnmatch(rel, pat) or fnmatch(rel, pat.rstrip("*").rstrip("/") + "/*")


def codeowner(rootdir: Path, rel_path: str) -> str | None:
    """Owner(s) of ``rel_path`` per CODEOWNERS: the last matching rule wins (GitHub's rule)."""
    owner = None
    for rule in codeowners_rules(rootdir):
        if _owner_match(rule.pattern, rel_path.replace(os.sep, "/")):
            owner = " ".join(rule.owners) or None
    return owner


def _marker_args(mark: Any) -> list[Any]:
    return [a if isinstance(a, (str, int, float, bool)) else str(a) for a in mark.args]


def _first_arg(item: Any, name: str) -> str | None:
    mark = item.get_closest_marker(name)
    if mark is None or not mark.args:
        return None
    return str(mark.args[0])


def _allure_labels(item: Any, label_type: str) -> list[str]:
    values: list[str] = []
    for mark in item.iter_markers(ALLURE_LABEL_MARKER):
        if mark.kwargs.get("label_type") == label_type:
            values.extend(str(a) for a in mark.args)
    return values


def case_from_item(item: Any, *, project_id: str = DEFAULT_PROJECT) -> CaseRecord:
    """One collected pytest item as a catalogue case (no test code is executed)."""
    nodeid = item.nodeid
    key = case_key_from_nodeid(nodeid)
    pin = _first_arg(item, CASE_ID_MARKER)
    func = getattr(item, "function", None)
    func = inspect.unwrap(func) if func is not None else None
    doc = inspect.getdoc(func) if func is not None else None
    first, rest = split_docstring(doc)
    title = getattr(func, "__allure_display_name__", None) or first
    if not title:
        title = _humanise(getattr(item, "originalname", None) or item.name)
    markers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mark in item.iter_markers():
        if mark.name == ALLURE_LABEL_MARKER:
            continue
        if mark.name in seen and mark.name not in (REQUIREMENT_MARKER, FEATURE_MARKER):
            continue
        seen.add(mark.name)
        args = _marker_args(mark) if mark.name in ARG_MARKERS else []
        entry = {"name": mark.name, "args": args}
        if entry not in markers:
            markers.append(entry)
    links: list[tuple[str, str]] = []
    for mark in item.iter_markers(REQUIREMENT_MARKER):
        links.extend(("requirement", str(a)) for a in mark.args)
    for mark in item.iter_markers(FEATURE_MARKER):
        links.extend(("feature", str(a)) for a in mark.args)
    links.extend(("feature", f) for f in _allure_labels(item, "feature"))
    defect = _first_arg(item, DEFECT_MARKER)
    if defect:
        links.append(("defect", defect))
    rel_path, lineno, _ = item.location
    rootdir = Path(str(item.config.rootpath))
    owner = _first_arg(item, OWNER_MARKER) or codeowner(rootdir, rel_path)
    severity = _allure_labels(item, "severity")
    priority = _first_arg(item, PRIORITY_MARKER) or (severity[0] if severity else None)
    qualname = key.partition("::")[2] or None
    module = getattr(getattr(item, "module", None), "__name__", None)
    now = utc_now()
    return CaseRecord(
        case_id=pin or case_id_for_key(key),
        case_key=key,
        project_id=project_id,
        framework="pytest",
        title=title,
        module=module,
        qualname=qualname,
        file_path=Path(rel_path).as_posix(),
        line=(lineno + 1) if isinstance(lineno, int) else None,
        description=rest,
        owner=owner,
        priority=priority,
        markers=tuple(markers),
        steps=tuple(extract_steps(func, doc)),
        links=tuple(dict.fromkeys(links)),
        status="active",
        first_seen_at=now,
        last_seen_at=now,
    )


# ---------------------------------------------------------------------- catalogue


@dataclass
class Catalogue:
    cases: list[CaseRecord] = field(default_factory=list)
    generated_at: str = field(default_factory=utc_now)
    complete: bool = True  # False: a filtered or erroring collection
    errors: list[str] = field(default_factory=list)
    revisions: list[RevisionRecord] = field(default_factory=list)  # stored, not exported

    def by_id(self) -> dict[str, CaseRecord]:
        return {c.case_id: c for c in self.cases}

    def to_json(self) -> dict[str, Any]:
        cases = []
        for case in sorted(self.cases, key=lambda c: c.case_key):
            data = case.to_json()
            for volatile in ("first_seen_at", "last_seen_at", "project_id"):
                data.pop(volatile, None)
            cases.append(data)
        return {
            "schema_version": 1,
            "generated_at": self.generated_at,
            "complete": self.complete,
            "case_count": len(cases),
            "cases": cases,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Catalogue:
        return cls(
            cases=[CaseRecord.from_json(c) for c in data.get("cases", [])],
            generated_at=str(data.get("generated_at", "")),
            complete=bool(data.get("complete", True)),
        )

    def to_markdown(self) -> str:
        """Grouped by module, then feature; one table row per case."""
        lines = ["# Test case catalogue", "", f"{len(self.cases)} case(s).", ""]
        by_module: dict[str, list[CaseRecord]] = {}
        for case in self.cases:
            by_module.setdefault(case.file_path or "(unknown)", []).append(case)
        for module in sorted(by_module):
            lines += [f"## {module}", ""]
            by_feature: dict[str, list[CaseRecord]] = {}
            for case in by_module[module]:
                features = [ref for kind, ref in case.links if kind == "feature"] or [""]
                by_feature.setdefault(features[0], []).append(case)
            for feature in sorted(by_feature):
                if feature:
                    lines += [f"### Feature: {feature}", ""]
                lines += [
                    "| Case | Title | Owner | Priority | Links | Steps |",
                    "|---|---|---|---|---|---|",
                ]
                for case in sorted(by_feature[feature], key=lambda c: c.case_key):
                    links = ", ".join(f"{k}:{r}" for k, r in case.links if k != "feature")
                    steps = "<br>".join(f"{i}. {s}" for i, s in enumerate(case.steps, 1))
                    lines.append(
                        "| `{}` | {} | {} | {} | {} | {} |".format(
                            case.case_id,
                            _md(case.title or ""),
                            _md(case.owner or ""),
                            _md(case.priority or ""),
                            _md(links),
                            _md(steps, keep_br=True),
                        )
                    )
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _md(text: str, *, keep_br: bool = False) -> str:
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if keep_br else text.replace("<br>", " ")


@dataclass
class CatalogueDiff:
    added: list[CaseRecord] = field(default_factory=list)
    removed: list[CaseRecord] = field(default_factory=list)
    renamed: list[tuple[CaseRecord, CaseRecord]] = field(default_factory=list)  # (old, new)
    changed: list[tuple[CaseRecord, CaseRecord, list[str]]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.renamed or self.changed)

    def to_json(self) -> dict[str, Any]:
        return {
            "added": [c.case_key for c in self.added],
            "removed": [c.case_key for c in self.removed],
            "renamed": [
                {"case_id": n.case_id, "from": o.case_key, "to": n.case_key}
                for o, n in self.renamed
            ],
            "changed": [{"case_id": n.case_id, "fields": f} for _, n, f in self.changed],
        }

    def to_markdown(self) -> str:
        if self.empty:
            return "No test case changes.\n"
        out = ["### Test case changes", ""]
        for label, items in (("Added", self.added), ("Removed", self.removed)):
            if items:
                out.append(f"**{label}** ({len(items)})")
                out += [f"- `{c.case_key}`" for c in items]
                out.append("")
        if self.renamed:
            out.append(f"**Renamed (pinned id kept)** ({len(self.renamed)})")
            out += [f"- `{o.case_key}` -> `{n.case_key}`" for o, n in self.renamed]
            out.append("")
        if self.changed:
            out.append(f"**Changed** ({len(self.changed)})")
            out += [f"- `{n.case_key}`: {', '.join(f)}" for _, n, f in self.changed]
            out.append("")
        return "\n".join(out)


_COMPARED = (
    "title",
    "description",
    "owner",
    "priority",
    "markers",
    "steps",
    "links",
    "current_revision_id",
)


def diff_catalogues(old: Catalogue, new: Catalogue) -> CatalogueDiff:
    """Added, removed, renamed-by-pin and changed cases between two catalogues."""
    before, after = old.by_id(), new.by_id()
    diff = CatalogueDiff()
    for case_id, case in after.items():
        prev = before.get(case_id)
        if prev is None:
            diff.added.append(case)
            continue
        if prev.case_key != case.case_key:
            diff.renamed.append((prev, case))
        fields_changed = [f for f in _COMPARED if getattr(prev, f) != getattr(case, f)]
        if fields_changed:
            diff.changed.append((prev, case, fields_changed))
    diff.removed = [c for cid, c in before.items() if cid not in after]
    for bucket in (diff.added, diff.removed):
        bucket.sort(key=lambda c: c.case_key)
    return diff


# ---------------------------------------------------------------------- building

COLLECTOR_OUT_ENV = "RESULTS_CATALOGUE_OUT"


def build_catalogue(rootdir: Path, args: Sequence[str] = ()) -> Catalogue:
    """Collect (``--collect-only``, never runs a test) under ``rootdir`` into a catalogue.

    Runs pytest in a subprocess so the caller's imports and plugins stay untouched. With
    no ``args`` the collection covers the configured ``testpaths`` and is *complete*; any
    selection argument or collection error makes it partial, and a partial catalogue
    never marks cases removed.
    """
    rootdir = Path(rootdir).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "cases.json"
        env = {**os.environ, COLLECTOR_OUT_ENV: str(out), "RESULTS_STORE": "off"}
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH")) if p
        )
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "results._catalogue_collector",
            "-p",
            "no:cacheprovider",
            *args,
        ]
        proc = subprocess.run(cmd, cwd=rootdir, env=env, capture_output=True, text=True)
        data = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {"cases": []}
    errors = list(data.get("errors", []))
    if proc.returncode not in (0, 5):
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
        errors.append(f"pytest exited {proc.returncode}: " + " | ".join(tail))
    return Catalogue(
        cases=[CaseRecord.from_json(c) for c in data.get("cases", [])],
        complete=not args and not errors,
        errors=errors,
        revisions=[RevisionRecord.from_json(r) for r in data.get("revisions", [])],
    )


def write_catalogue(catalogue: Catalogue, out_dir: Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / "catalogue.json", out_dir / "catalogue.md"
    json_path.write_text(json.dumps(catalogue.to_json(), indent=2, ensure_ascii=False) + "\n")
    md_path.write_text(catalogue.to_markdown(), encoding="utf-8")
    return json_path, md_path


def store_catalogue(store: Any, catalogue: Catalogue) -> int:
    """Upsert the catalogue; mark absent cases removed only when the collection was complete."""
    store.upsert_cases(catalogue.cases)
    if catalogue.revisions:
        store.add_revisions(catalogue.revisions)
    if not catalogue.complete:
        return 0
    return store.mark_removed(c.case_id for c in catalogue.cases)


def load_catalogue(path: Path) -> Catalogue:
    return Catalogue.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def catalogue_from_store(
    store: Any, *, project_id: str = DEFAULT_PROJECT, **filters: Any
) -> Catalogue:
    """The stored catalogue (optionally filtered like ``GET /cases``)."""
    from results.store import row_to_case

    links: dict[str, list[tuple[str, str]]] = {}
    for row in store.query("case_links_all", project_id=project_id):
        links.setdefault(row["case_id"], []).append((row["kind"], row["ref"]))
    cases = []
    for row in store.query("cases_list", project_id=project_id, limit=1_000_000, **filters):
        detail = store.query("case_get", case_id=row["case_id"], project_id=project_id)
        merged = {**row, "steps_json": detail[0]["steps_json"] if detail else None}
        cases.append(row_to_case(merged, links.get(row["case_id"], [])))
    return Catalogue(cases=cases)


def register_markers(config: Any) -> None:
    for line in MARKER_LINES:
        config.addinivalue_line("markers", line)


def cases_from_items(items: Iterable[Any]) -> list[CaseRecord]:
    seen: dict[str, CaseRecord] = {}
    for item in items:
        case = case_from_item(item)
        seen.setdefault(case.case_id, case)
    return list(seen.values())


__all__ = [
    "Catalogue",
    "CatalogueDiff",
    "build_catalogue",
    "case_from_item",
    "catalogue_from_store",
    "case_id_for_key",
    "case_id_for_nodeid",
    "case_key_from_nodeid",
    "cases_from_items",
    "codeowner",
    "diff_catalogues",
    "extract_steps",
    "load_catalogue",
    "register_markers",
    "store_catalogue",
    "write_catalogue",
]
