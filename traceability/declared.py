"""The defect ids the suite declares, found statically, and the offline checks over them.

**Declared ids.** An AST pass over the test sources (comments and string literals are never
read as code) finds every detector call site (``still_reproduces``, ``known_defect`` and the
other names in ``defects/tracking.yaml``) and takes its id from the first argument or a
``ticket=`` keyword, resolving a module-level constant when the id is a name. Open issues in
``*.manifest.json`` files are declared too. Each id carries the expectation its call states:

=================  =====================================================================
open               ``closed=False``, or an open issue in a manifest
closed             ``closed=True``
env-dependent      no ``closed=`` (resolved at run time from the snapshot or environment)
=================  =====================================================================

**Committed snapshot.** ``defects refresh --keys-from declared --out <path>`` asks the
trackers about exactly these ids and writes a snapshot to commit. The offline checks read
only that file and never call a tracker:

* every declared id has an entry (else "refresh with `defects sync`");
* the scan found at least ``min_call_sites`` call sites and ``min_ids`` ids, so a narrowed
  scan cannot pass by tracking nothing;
* drift: a declared ``open`` id the snapshot says is closed, or a ``closed`` one it says is
  open, is a line that must be in the drift baseline. A baseline line that no longer
  drifts fails too ("stale, delete it"): the baseline only shrinks.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from traceability._util import load_yaml
from traceability.requirements import Namespace

CONFIG = Path("defects") / "tracking.yaml"
OPEN, CLOSED, ENV = "open", "closed", "env-dependent"
DEFAULT_CALLS = ("still_reproduces", "known_defect")
MANIFEST_GLOB = "*.manifest.json"


@dataclass(frozen=True)
class TrackingConfig:
    roots: tuple[str, ...] = ("tests",)
    exclude: tuple[str, ...] = ()
    detector_calls: tuple[str, ...] = DEFAULT_CALLS
    min_call_sites: int = 0
    min_ids: int = 0
    snapshot: str | None = None
    drift_baseline: str | None = None
    rootdir: Path = Path(".")

    @classmethod
    def load(cls, path: str | Path | None = None, rootdir: Path | None = None) -> TrackingConfig:
        root = Path(rootdir) if rootdir is not None else Path.cwd()
        target = Path(path) if path else root / CONFIG
        body = load_yaml(target) if target.is_file() else {}
        body = body or {}
        if not isinstance(body, dict):
            raise ValueError(f"{target}: a tracking config is a mapping")
        return cls(
            roots=tuple(body.get("roots") or ("tests",)),
            exclude=tuple(body.get("exclude") or ()),
            detector_calls=tuple(body.get("detector_calls") or DEFAULT_CALLS),
            min_call_sites=int(body.get("min_call_sites") or 0),
            min_ids=int(body.get("min_ids") or 0),
            snapshot=body.get("snapshot") or None,
            drift_baseline=body.get("drift_baseline") or None,
            rootdir=root,
        )

    def path(self, value: str | None) -> Path | None:
        return None if not value else (self.rootdir / value)


@dataclass(frozen=True)
class Declared:
    id: str
    expectation: str
    where: str


@dataclass
class Scan:
    declared: list[Declared] = field(default_factory=list)
    call_sites: int = 0
    unresolved: list[str] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return sorted({d.id for d in self.declared})

    def expectations(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for d in self.declared:
            out.setdefault(d.id, set()).add(d.expectation)
        return out


def _files(config: TrackingConfig, pattern: str) -> list[Path]:
    excluded = [config.rootdir / e for e in config.exclude]
    out = []
    for root in config.roots:
        base = config.rootdir / root
        if not base.exists():
            continue
        for path in sorted(base.rglob(pattern)):
            if any(path.is_relative_to(e) for e in excluded) or ".venv" in path.parts:
                continue
            out.append(path)
    return out


def _constants(trees: Iterable[ast.Module]) -> dict[str, str]:
    """Module-level ``NAME = "..."`` string constants, by bare name, across the scanned files."""
    out: dict[str, str] = {}
    for tree in trees:
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            out.setdefault(target.id, node.value.value)
    return out


def _call_matches(node: ast.Call, calls: set[str]) -> bool:
    """``name`` matches any call of that name; ``module.name`` only that qualified call."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in calls
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else None
        return func.attr in calls or (owner is not None and f"{owner}.{func.attr}" in calls)
    return False


def _resolve(node: ast.AST | None, constants: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute):
        return constants.get(node.attr)
    return None


def _expectation(node: ast.Call) -> str:
    for kw in node.keywords:
        if kw.arg == "closed" and isinstance(kw.value, ast.Constant):
            if kw.value.value is True:
                return CLOSED
            if kw.value.value is False:
                return OPEN
    return ENV


def scan(config: TrackingConfig, namespace: Namespace | None = None) -> Scan:
    """Every declared defect id under the configured roots (offline, static)."""
    ns = namespace or Namespace.from_env()
    parsed: list[tuple[Path, ast.Module]] = []
    for path in _files(config, "*.py"):
        try:
            parsed.append((path, ast.parse(path.read_text(encoding="utf-8"))))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
    constants = _constants(tree for _, tree in parsed)
    result = Scan()
    calls = set(config.detector_calls)
    for path, tree in parsed:
        rel = path.relative_to(config.rootdir).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _call_matches(node, calls):
                continue
            raw = node.args[0] if node.args else None
            for kw in node.keywords:
                if kw.arg == "ticket":
                    raw = kw.value
            if raw is None:
                continue  # the function's own definition or a call without an id
            result.call_sites += 1
            value = _resolve(raw, constants)
            where = f"{rel}:{node.lineno}"
            if value is None:
                result.unresolved.append(where)
            elif not ns.is_requirement_id(value):
                result.declared.append(Declared(value, _expectation(node), where))
    for path in _files(config, MANIFEST_GLOB):
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rel = path.relative_to(config.rootdir).as_posix()
        for entry in entries if isinstance(entries, list) else ():
            for issue in (entry or {}).get("open_issues") or ():
                if issue.get("issue"):
                    result.declared.append(Declared(str(issue["issue"]), OPEN, rel))
    return result


# ---------------------------------------------------------------------- checks


def missing_ids(ids: Iterable[str], snapshot_defects: Mapping[str, Any]) -> list[str]:
    return sorted(i for i in set(ids) if i not in snapshot_defects)


def drift_lines(scan_result: Scan, snapshot_defects: Mapping[str, Any]) -> list[str]:
    """``<id> declared <exp> tracker <state>`` for every declared expectation the tracker denies."""
    out = []
    for defect_id, expectations in sorted(scan_result.expectations().items()):
        entry = snapshot_defects.get(defect_id)
        state = getattr(entry, "state", None) or (entry or {}).get("state") if entry else None
        if len(expectations - {ENV}) > 1:
            out.append(f"{defect_id} declared open-and-closed tracker {state or 'missing'}")
            continue
        for exp in expectations - {ENV}:
            if (exp, state) in ((OPEN, "closed"), (CLOSED, "open")):
                out.append(f"{defect_id} declared {exp} tracker {state}")
    return out


def load_lines(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    raw = path.read_text(encoding="utf-8").splitlines()
    return {" ".join(x.split()) for x in raw if x.strip() and not x.lstrip().startswith("#")}


@dataclass
class CheckResult:
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check(
    config: TrackingConfig,
    snapshot_defects: Mapping[str, Any],
    scan_result: Scan | None = None,
) -> CheckResult:
    """The offline gate: missing ids, the anti-shrink floor, and the drift ratchet."""
    found = scan_result or scan(config)
    out = CheckResult()
    for defect_id in missing_ids(found.ids, snapshot_defects):
        out.problems.append(
            f"MISSING FROM SNAPSHOT: {defect_id} (refresh with `defects sync`, or "
            f"`defects refresh --keys-from declared`)"
        )
    if found.call_sites < config.min_call_sites:
        out.problems.append(
            f"FLOOR: {found.call_sites} detector call site(s) found, at least "
            f"{config.min_call_sites} expected: the scan narrowed, it did not get cleaner"
        )
    if len(found.ids) < config.min_ids:
        out.problems.append(
            f"FLOOR: {len(found.ids)} declared id(s) found, at least {config.min_ids} expected"
        )
    baseline = load_lines(config.path(config.drift_baseline))
    current = set(drift_lines(found, snapshot_defects))
    out.problems += [
        f"DRIFT: {line} (not in the drift baseline)" for line in sorted(current - baseline)
    ]
    out.problems += [
        f"STALE DRIFT BASELINE LINE, delete it: {line}" for line in sorted(baseline - current)
    ]
    out.notes += [f"unresolved id at {where}" for where in found.unresolved]
    return out


def age_line(generated_at: str | None, now: datetime | None = None) -> str:
    """``snapshot <date> (<n> days old)``; an unreadable stamp says so."""
    stamp = parse_time(generated_at)
    if stamp is None:
        return f"snapshot {generated_at or 'with no date'} (age unknown)"
    days = ((now or datetime.now(tz=timezone.utc)) - stamp).days
    return f"snapshot {stamp.date().isoformat()} ({days} days old)"


def parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
