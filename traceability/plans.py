"""Test plans: a reviewed YAML file that names a scope and the exit criteria for it.

``plans/<plan-id>.yaml`` (schema ``traceability/schemas/plan.schema.json``)::

    id: release-1-2
    title: Release 1.2 regression
    release: "1.2"
    environments: [qa]
    scope:
      include:
        - marker: "smoke or api"
        - requirements: [REQ-1, REQ-2]
        - requirement_labels: [billing]
        - nodeids: ["tests/demo_examples/finance/"]
      exclude:
        - marker: slow
    sections:                      # optional; owners are free text, no assignment workflow
      - {name: billing, owner: "@billing", requirements: [REQ-4]}
    exit_criteria:
      run_integrity: PASS
      min_pass_rate: 0.95
      no_failures_in_requirements: all
      requirements_proved: [REQ-4]
      max_open_defects: {any: 0}
      detectors_reached_verdict: true
      no_draft_evidence: true

Every include and exclude selector must match at least one test: a selector that matches
nothing is an error, not a quietly empty plan. ``pytest --plan plans/x.yaml`` deselects
everything outside the plan, so execution reuses the normal runner and gate.

Criteria report ``met``, ``unmet`` or ``no_evidence``; ``no_evidence`` never counts as met.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from results.revision import blob_sha
from traceability._util import load_yaml, utc_now, validate
from traceability.evidence import ResultSource, Scope, latest_per_test
from traceability.matrix import Status, build_matrix
from traceability.requirements import Registry

PLANS_DIR_ENV = "PLANS_DIR"
DEFAULT_PLANS_DIR = Path("plans")
MET, UNMET, NO_EVIDENCE = "met", "unmet", "no_evidence"


class PlanError(ValueError):
    """A plan that does not load, validate or resolve."""


@dataclass(frozen=True)
class Plan:
    id: str
    title: str
    path: Path
    body: dict[str, Any]
    revision: str

    @property
    def release(self) -> str | None:
        rel = self.body.get("release")
        return None if rel is None else str(rel)

    @property
    def include(self) -> list[dict[str, Any]]:
        return list((self.body.get("scope") or {}).get("include") or [])

    @property
    def exclude(self) -> list[dict[str, Any]]:
        return list((self.body.get("scope") or {}).get("exclude") or [])

    @property
    def criteria(self) -> dict[str, Any]:
        return dict(self.body.get("exit_criteria") or {})


def plans_dir() -> Path:
    return Path(os.environ.get(PLANS_DIR_ENV) or DEFAULT_PLANS_DIR)


def load_plan(path: str | Path) -> Plan:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PlanError(f"{path}: {exc}") from exc
    body = load_yaml(path)
    if not isinstance(body, dict):
        raise PlanError(f"{path}: a plan is a mapping")
    try:
        validate(body, "plan.schema.json")
    except ValueError as exc:
        raise PlanError(f"{path}: {exc}") from exc
    if body["id"] != path.stem:
        raise PlanError(f"{path}: id {body['id']!r} must equal the file name {path.stem!r}")
    return Plan(body["id"], str(body.get("title") or body["id"]), path, body, blob_sha(raw))


def load_plans(directory: str | Path | None = None) -> list[Plan]:
    d = Path(directory) if directory is not None else plans_dir()
    return [load_plan(p) for p in sorted(d.glob("*.yaml"))]


# ------------------------------------------------------------------ selection


def marker_matches(expression: str, names: Iterable[str]) -> bool:
    """pytest's own ``-m`` grammar, evaluated against marker names."""
    from _pytest.mark.expression import Expression  # the same parser ``-m`` uses

    present = frozenset(names)
    try:
        return Expression.compile(expression).evaluate(lambda name, **_kw: name in present)
    except Exception as exc:  # ParseError and friends
        raise PlanError(f"marker expression {expression!r}: {exc}") from exc


def nodeid_matches(pattern: str, nodeid: str) -> bool:
    """Glob when the pattern has a wildcard, else a node id or path prefix."""
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(nodeid, pattern)
    if nodeid == pattern:
        return True
    base = pattern.rstrip("/")
    return nodeid.startswith((base + "/", base + "::"))


def _selector_key(sel: Mapping[str, Any]) -> str:
    kind, value = next(iter(sel.items()))
    return f"{kind}: {value if isinstance(value, str) else ', '.join(map(str, value))}"


def _matcher(sel: Mapping[str, Any], registry: Registry) -> Callable[[Mapping[str, Any]], bool]:
    kind, value = next(iter(sel.items()))
    if kind == "marker":
        return lambda item: marker_matches(value, item.get("markers") or ())
    if kind == "requirements":
        wanted = set(value)
        return lambda item: bool(wanted & set(item.get("requirements") or ()))
    if kind == "requirement_labels":
        labels = set(value)
        wanted = {r.id for r in registry if labels & set(r.labels)}
        return lambda item: bool(wanted & set(item.get("requirements") or ()))
    if kind == "nodeids":
        return lambda item: any(nodeid_matches(p, item["nodeid"]) for p in value)
    raise PlanError(f"unknown selector {kind!r}")


@dataclass
class Selection:
    nodeids: list[str] = field(default_factory=list)
    unmatched_selectors: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.nodeids) and not self.unmatched_selectors and not self.errors

    def to_json(self) -> dict[str, Any]:
        return {
            "nodeids": self.nodeids,
            "count": len(self.nodeids),
            "unmatched_selectors": self.unmatched_selectors,
            "requirements": self.requirements,
            "errors": self.errors,
        }


def plan_requirements(plan: Plan, registry: Registry) -> list[str]:
    """Requirement ids the plan names directly (ids and labels) plus its sections'."""
    ids: list[str] = []
    for sel in plan.include:
        if "requirements" in sel:
            ids.extend(sel["requirements"])
        if "requirement_labels" in sel:
            labels = set(sel["requirement_labels"])
            ids.extend(r.id for r in registry if labels & set(r.labels))
    for section in plan.body.get("sections") or ():
        ids.extend(section.get("requirements") or ())
    return list(dict.fromkeys(ids))


def select(plan: Plan, items: Sequence[Mapping[str, Any]], registry: Registry) -> Selection:
    """Apply the plan's selectors to collected items (``nodeid``, ``markers``, ``requirements``)."""
    unmatched: list[str] = []
    chosen: set[str] = set()
    for sel in plan.include:
        match = _matcher(sel, registry)
        hits = {i["nodeid"] for i in items if match(i)}
        if not hits:
            unmatched.append(f"include {_selector_key(sel)}")
        chosen |= hits
    if not plan.include:
        chosen = {i["nodeid"] for i in items}
    for sel in plan.exclude:
        match = _matcher(sel, registry)
        hits = {i["nodeid"] for i in items if match(i)}
        if not hits:
            unmatched.append(f"exclude {_selector_key(sel)}")
        chosen -= hits
    nodeids = [i["nodeid"] for i in items if i["nodeid"] in chosen]
    linked = {r for i in items if i["nodeid"] in chosen for r in i.get("requirements") or ()}
    reqs = list(dict.fromkeys([*plan_requirements(plan, registry), *sorted(linked)]))
    return Selection(nodeids, unmatched, reqs)


def resolve_plan(plan: Plan, rootdir: Path, registry: Registry | None = None) -> Selection:
    """Collect the suite (never run it) and apply the plan."""
    from traceability.collect import collect

    registry = registry if registry is not None else Registry.load(rootdir=rootdir)
    collection = collect(rootdir)
    sel = select(plan, collection.items, registry)
    sel.errors.extend(collection.errors)
    return sel


def lint_plans(directory: Path, rootdir: Path) -> list[str]:
    """Every plan validates and every selector of every plan matches at least one test."""
    from traceability.collect import collect

    problems: list[str] = []
    paths = sorted(Path(directory).glob("*.yaml"))
    plans = []
    for path in paths:
        try:
            plans.append(load_plan(path))
        except (PlanError, ValueError) as exc:
            problems.append(str(exc))
    if not plans:
        return problems
    registry = Registry.load(rootdir=rootdir)
    collection = collect(rootdir)
    problems.extend(f"collection: {e}" for e in collection.errors)
    for plan in plans:
        try:
            sel = select(plan, collection.items, registry)
        except PlanError as exc:
            problems.append(f"{plan.path}: {exc}")
            continue
        problems.extend(
            f"{plan.path}: selector matches nothing: {u}" for u in sel.unmatched_selectors
        )
        if not sel.nodeids:
            problems.append(f"{plan.path}: the plan selects no test")
        unknown = [r for r in plan_requirements(plan, registry) if registry.status(r) != "active"]
        problems.extend(
            f"{plan.path}: requirement {r} is not active in the registry" for r in unknown
        )
    return problems


# ------------------------------------------------------------------ evaluation


@dataclass(frozen=True)
class CriterionResult:
    name: str
    result: str  # met | unmet | no_evidence
    detail: str
    expected: Any = None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "result": self.result,
            "detail": self.detail,
            "expected": self.expected,
        }


@dataclass
class PlanEvaluation:
    plan_id: str
    plan_revision: str
    run_ids: list[str]
    criteria: list[CriterionResult]
    evaluated_at: str = field(default_factory=utc_now)

    @property
    def result(self) -> str:
        results = {c.result for c in self.criteria}
        if UNMET in results:
            return UNMET
        if NO_EVIDENCE in results or not self.criteria:
            return NO_EVIDENCE
        return MET

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "plan_id": self.plan_id,
            "plan_revision": self.plan_revision,
            "evaluated_at": self.evaluated_at,
            "run_ids": self.run_ids,
            "result": self.result,
            "criteria": [c.to_json() for c in self.criteria],
        }

    def to_markdown(self) -> str:
        lines = [
            f"## Plan {self.plan_id}: {self.result.upper()}",
            "",
            f"Runs: {', '.join(self.run_ids) or 'none'} · "
            f"plan revision `{self.plan_revision[:12]}`",
            "",
            "| Criterion | Result | Detail |",
            "|---|---|---|",
        ]
        lines += [
            f"| {c.name} | {c.result} | {c.detail.replace('|', '/')} |" for c in self.criteria
        ]
        return "\n".join(lines) + "\n"


# A criterion evaluator: (expected value, context) -> CriterionResult.
Evaluator = Callable[[str, Any, "EvalContext"], CriterionResult]
EVALUATORS: dict[str, Evaluator] = {}


def criterion(name: str):
    def wrap(func: Evaluator) -> Evaluator:
        EVALUATORS[name] = func
        return func

    return wrap


@dataclass
class EvalContext:
    plan: Plan
    source: ResultSource
    registry: Registry
    selection: Selection | None
    open_defects: Callable[[], list[Mapping[str, Any]]] | None = None
    _matrix: Any = None

    def requirement_ids(self, expected: Any) -> list[str]:
        if expected == "all":
            base = self.selection.requirements if self.selection else []
            return base or plan_requirements(self.plan, self.registry)
        return list(expected or [])

    def matrix(self, ids: Sequence[str]):
        return build_matrix(self.registry, None, self.source, Scope(requirements=tuple(ids)))

    def latest(self):
        latest = latest_per_test(self.source.results(), self.source.runs())
        if self.selection is not None:
            wanted = set(self.selection.nodeids)
            latest = {k: v for k, v in latest.items() if k in wanted}
        return latest


@criterion("run_integrity")
def _run_integrity(name: str, expected: Any, ctx: EvalContext) -> CriterionResult:
    runs = ctx.source.runs()
    if not runs:
        return CriterionResult(name, NO_EVIDENCE, "no run in scope", expected)
    bad = [
        f"{r.run_id}/{r.lane}={r.integrity or 'no marker'}" for r in runs if r.integrity != expected
    ]
    if bad:
        return CriterionResult(
            name, UNMET, "runs not " + str(expected) + ": " + ", ".join(bad), expected
        )
    return CriterionResult(name, MET, f"{len(runs)} run(s) {expected}", expected)


@criterion("min_pass_rate")
def _min_pass_rate(name: str, expected: Any, ctx: EvalContext) -> CriterionResult:
    latest = ctx.latest().values()
    passed = sum(1 for r in latest if r.outcome == "passed")
    denom = sum(1 for r in latest if r.outcome in ("passed", "failed", "error"))
    if denom == 0:
        return CriterionResult(name, NO_EVIDENCE, "no test executed", expected)
    rate = passed / denom
    detail = f"{passed}/{denom} passed ({rate:.1%}, need {float(expected):.1%})"
    return CriterionResult(name, MET if rate >= float(expected) else UNMET, detail, expected)


def _status_criterion(name, expected, ctx, ok: set[Status], bad: set[Status]) -> CriterionResult:
    ids = ctx.requirement_ids(expected)
    if not ids:
        return CriterionResult(name, NO_EVIDENCE, "the plan names no requirement", expected)
    matrix = ctx.matrix(ids)
    failing = [f"{r.requirement_id} {r.status.value}" for r in matrix.rows if r.status in bad]
    missing = [
        f"{r.requirement_id} {r.status.value}"
        for r in matrix.rows
        if r.status not in ok and r.status not in bad
    ]
    if failing:
        return CriterionResult(name, UNMET, "; ".join(failing), expected)
    if missing:
        return CriterionResult(name, NO_EVIDENCE, "; ".join(missing), expected)
    return CriterionResult(name, MET, f"{len(ids)} requirement(s)", expected)


@criterion("no_failures_in_requirements")
def _no_failures(name: str, expected: Any, ctx: EvalContext) -> CriterionResult:
    return _status_criterion(
        name,
        expected,
        ctx,
        ok={Status.PASSING, Status.PROVED, Status.DRAFT_ONLY},
        bad={Status.FAILING, Status.KNOWN_DEFECT},
    )


@criterion("requirements_proved")
def _proved(name: str, expected: Any, ctx: EvalContext) -> CriterionResult:
    return _status_criterion(
        name, expected, ctx, ok={Status.PROVED}, bad={Status.FAILING, Status.KNOWN_DEFECT}
    )


@criterion("detectors_reached_verdict")
def _detectors(name: str, expected: Any, ctx: EvalContext) -> CriterionResult:
    reds = [v for v in ctx.source.verdicts() if v.get("kind") == "expected_red"]
    if ctx.selection is not None:
        wanted = set(ctx.selection.nodeids)
        reds = [v for v in reds if v.get("nodeid") in wanted]
    if not reds:
        return CriterionResult(name, NO_EVIDENCE, "no detector verdict in scope", expected)
    silent = sorted({str(v.get("ticket")) for v in reds if v.get("verdict") == "held_empty"})
    if silent and expected:
        return CriterionResult(name, UNMET, "not exercised: " + ", ".join(silent), expected)
    return CriterionResult(name, MET, f"{len(reds)} detector verdict(s)", expected)


@criterion("no_draft_evidence")
def _no_draft(name: str, expected: Any, ctx: EvalContext) -> CriterionResult:
    ids = ctx.requirement_ids("all")
    if not ids:
        return CriterionResult(name, NO_EVIDENCE, "the plan names no requirement", expected)
    drafts = [r.requirement_id for r in ctx.matrix(ids).rows if r.status is Status.DRAFT_ONLY]
    if drafts and expected:
        return CriterionResult(name, UNMET, "only draft tests back " + ", ".join(drafts), expected)
    return CriterionResult(name, MET, "no requirement rests on draft tests alone", expected)


@criterion("max_open_defects")
def _max_open_defects(name: str, expected: Any, ctx: EvalContext) -> CriterionResult:
    if ctx.open_defects is None:
        return CriterionResult(name, NO_EVIDENCE, "no defect tracker configured", expected)
    try:
        defects = ctx.open_defects()
    except Exception as exc:  # an unreachable tracker is no evidence, never "zero open"
        return CriterionResult(name, NO_EVIDENCE, f"defect tracker unreachable: {exc}", expected)
    over = []
    for severity, limit in dict(expected).items():
        count = sum(1 for d in defects if severity == "any" or d.get("severity") == severity)
        if count > int(limit):
            over.append(f"{severity}: {count} open > {limit}")
    if over:
        return CriterionResult(name, UNMET, "; ".join(over), expected)
    return CriterionResult(name, MET, f"{len(defects)} open defect(s) within limits", expected)


def evaluate_plan(
    plan: Plan,
    results: ResultSource,
    run_ids: Sequence[str] = (),
    *,
    registry: Registry | None = None,
    selection: Selection | None = None,
    open_defects: Callable[[], list[Mapping[str, Any]]] | None = None,
) -> PlanEvaluation:
    registry = registry if registry is not None else Registry.load()
    ctx = EvalContext(plan, results, registry, selection, open_defects)
    out = []
    for name, expected in plan.criteria.items():
        out.append(EVALUATORS[name](name, expected, ctx))
    ids = list(run_ids) or list(dict.fromkeys(r.run_id for r in results.runs()))
    return PlanEvaluation(plan.id, plan.revision, ids, out)


__all__ = [
    "Plan",
    "PlanError",
    "PlanEvaluation",
    "Selection",
    "evaluate_plan",
    "lint_plans",
    "load_plan",
    "load_plans",
    "resolve_plan",
    "select",
]
