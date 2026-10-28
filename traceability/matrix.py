"""Requirements traceability matrix: requirement -> linked cases -> latest result in scope.

Each requirement gets exactly one status, chosen by an explicit precedence so a cell never
overstates the evidence:

=============  ===============================================================
UNCOVERED      no test links to it
NOT_RUN        linked tests exist, none executed in scope
UNVERIFIED     results exist only from runs whose run-trust verdict is FAIL
FAILING        a linked test failed (and the failure is not a tracked defect)
FIX_OBSERVED   a linked detector stopped reproducing (``cleared``): verify, then unwrap it
FLAKY          a linked test failed and then passed on a rerun in the same run
KNOWN_DEFECT   linked tests fail, explained by an expected-red ``reproduces`` verdict
DRAFT_ONLY     only generated tests awaiting review passed
PASSING        linked tests passed; no coverage verdict proved the requirement
PROVED         a passing test recorded a ``proved`` coverage verdict naming it
=============  ===============================================================

``stale`` is a separate flag: the result came from an older revision of the test. It is
only measurable from the store (revisions are recorded there); from artefacts it is
reported as unmeasured, never as "fresh".

A registry entry of ``kind`` ``epic`` or ``container`` is too coarse to be the requirement a
test verifies; a link to one is listed as a finding ("link too coarse"), report only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from traceability._util import utc_now, validate
from traceability.evidence import (
    EXECUTED,
    FAILING_OUTCOMES,
    ResultSource,
    RunInfo,
    Scope,
    latest_per_test,
)
from traceability.links import CaseLink
from traceability.requirements import Namespace, Registry, ids_in_verdict


class Status(str, Enum):
    FAILING = "FAILING"
    FIX_OBSERVED = "FIX_OBSERVED"
    FLAKY = "FLAKY"
    UNCOVERED = "UNCOVERED"
    UNVERIFIED = "UNVERIFIED"
    NOT_RUN = "NOT_RUN"
    KNOWN_DEFECT = "KNOWN_DEFECT"
    DRAFT_ONLY = "DRAFT_ONLY"
    PASSING = "PASSING"
    PROVED = "PROVED"


ORDER = list(Status)  # listing order: the problems first
_COVERAGE_RANK = {"proved": 4, "recorded": 3, "held_empty": 2, "abstained": 1}
_RED_FAILING = frozenset({"signature_mismatch", "closed_regressed"})
CONTAINER_KINDS = frozenset({"epic", "container"})
CSV_COLUMNS = (
    "requirement",
    "status",
    "nodeid",
    "outcome",
    "run_id",
    "coverage_verdict",
    "detector_verdict",
    "defects",
)


@dataclass(frozen=True)
class Cell:
    nodeid: str
    case_id: str | None
    outcome: str
    run_id: str | None
    run_integrity: str | None
    state: str  # not_run | unverified | failing | fix_observed | flaky | known_defect | passed
    coverage_verdict: str | None = None
    detector_verdict: str | None = None
    defects: tuple[str, ...] = ()
    draft: bool = False
    stale: bool | None = None
    origins: tuple[str, ...] = ()
    first_failure: str | None = None  # a flaky cell's first failed attempt

    def to_json(self) -> dict[str, Any]:
        return {
            "nodeid": self.nodeid,
            "case_id": self.case_id,
            "outcome": self.outcome,
            "run_id": self.run_id,
            "run_integrity": self.run_integrity,
            "state": self.state,
            "coverage_verdict": self.coverage_verdict,
            "detector_verdict": self.detector_verdict,
            "defects": list(self.defects),
            "draft": self.draft,
            "stale": self.stale,
            "origins": list(self.origins),
            "first_failure": self.first_failure,
        }


@dataclass
class Row:
    requirement_id: str
    title: str
    priority: str | None
    status: Status
    cells: list[Cell] = field(default_factory=list)

    @property
    def stale(self) -> bool | None:
        flags = [c.stale for c in self.cells if c.state not in ("not_run",)]
        if any(f is True for f in flags):
            return True
        return False if flags and all(f is False for f in flags) else None

    def to_json(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "title": self.title,
            "priority": self.priority,
            "status": self.status.value,
            "stale": self.stale,
            "cases": [c.to_json() for c in self.cells],
        }


@dataclass
class Matrix:
    scope: dict[str, Any]
    run_ids: list[str]
    rows: list[Row]
    unregistered: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)  # report only
    results_read: int = 0
    generated_at: str = field(default_factory=utc_now)

    def counts(self) -> dict[str, int]:
        return {s.value: sum(1 for r in self.rows if r.status is s) for s in Status}

    def row(self, requirement_id: str) -> Row | None:
        return next((r for r in self.rows if r.requirement_id == requirement_id), None)

    @property
    def headline(self) -> str:
        total = len(self.rows)
        counts = self.counts()
        if total == 0:
            return "NO REQUIREMENTS IN SCOPE"
        proved = counts[Status.PROVED.value]
        problems = counts[Status.FAILING.value] + counts[Status.UNCOVERED.value]
        tail = f"; {problems} failing or uncovered" if problems else ""
        if proved == 0:
            return (
                f"NOTHING IS PROVED: 0 of {total} requirements has a proved coverage verdict "
                f"({counts[Status.PASSING.value]} pass on linked tests alone){tail}"
            )
        return f"{proved} of {total} requirements proved{tail}"

    @property
    def headline_line(self) -> str:
        """One machine-readable line: ``MATRIX requirements=<n> results=<m> <STATUS>=<k> ...``.

        A matrix built from no results at all ends with ``NO-EVIDENCE``: every status in it
        is then an absence of measurement, not a finding.
        """
        counts = " ".join(f"{k}={v}" for k, v in self.counts().items() if v)
        line = f"MATRIX requirements={len(self.rows)} results={self.results_read}"
        line += f" {counts}" if counts else ""
        return line + (" NO-EVIDENCE" if self.results_read == 0 else "")

    def csv_rows(self) -> list[dict[str, str]]:
        """One row per requirement x linked test; a requirement with none gets one empty row."""
        out = []
        for row in self.rows:
            base = {"requirement": row.requirement_id, "status": row.status.value}
            if not row.cells:
                out.append({**base, **dict.fromkeys(CSV_COLUMNS[2:], "")})
            for c in row.cells:
                out.append(
                    {
                        **base,
                        "nodeid": c.nodeid,
                        "outcome": c.outcome,
                        "run_id": c.run_id or "",
                        "coverage_verdict": c.coverage_verdict or "",
                        "detector_verdict": c.detector_verdict or "",
                        "defects": " ".join(c.defects),
                    }
                )
        return out

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scope": self.scope,
            "generated_at": self.generated_at,
            "run_ids": self.run_ids,
            "headline": self.headline,
            "counts": self.counts(),
            "rows": [r.to_json() for r in self.rows],
            "unregistered": self.unregistered,
            "findings": self.findings,
            "results_read": self.results_read,
        }


def _cell_state(
    outcome: str, integrity: str | None, red: str | None, failed_attempt: bool = False
) -> str:
    if outcome not in EXECUTED:
        return "not_run"
    if integrity == "FAIL":
        return "unverified"
    if red == "reproduces" or outcome == "xfailed":
        return "known_defect"
    if red == "cleared":
        return "fix_observed"
    if outcome in FAILING_OUTCOMES or red in _RED_FAILING:
        return "failing"
    if failed_attempt:
        return "flaky"
    return "passed"


def _status(cells: list[Cell]) -> Status:
    if not cells:
        return Status.UNCOVERED
    executed = [c for c in cells if c.state != "not_run"]
    if not executed:
        return Status.NOT_RUN
    evidence = [c for c in executed if c.state != "unverified"]
    if not evidence:
        return Status.UNVERIFIED
    if any(c.state == "failing" for c in evidence):
        return Status.FAILING
    if any(c.state == "fix_observed" for c in evidence):
        return Status.FIX_OBSERVED
    if any(c.state == "flaky" for c in evidence):
        return Status.FLAKY
    if any(c.state == "known_defect" for c in evidence):
        return Status.KNOWN_DEFECT
    approved = [c for c in evidence if c.state == "passed" and not c.draft]
    if not approved:
        return Status.DRAFT_ONLY
    if any(c.coverage_verdict == "proved" for c in approved):
        return Status.PROVED
    return Status.PASSING


def build_matrix(
    registry: Registry,
    links: Iterable[CaseLink] | None,
    results: ResultSource,
    scope: Scope | None = None,
) -> Matrix:
    """The matrix for every active requirement (or ``scope.requirements``)."""
    scope = scope or Scope()
    ns = Namespace.from_env()
    runs: list[RunInfo] = results.runs()
    integrity = {(r.run_id, r.lane): r.integrity for r in runs}
    observed = results.results()
    latest = latest_per_test(observed, runs)
    link_list = list(results.links().links if links is None else links)

    red: dict[tuple[str, str], str] = {}
    red_tickets: dict[tuple[str, str], str] = {}
    coverage: dict[tuple[str, str, str], str] = {}
    for record in results.verdicts():
        key = (str(record.get("run_id") or ""), str(record.get("nodeid") or ""))
        if record.get("kind") == "expected_red":
            red[key] = str(record.get("verdict"))
            if record.get("ticket"):
                red_tickets[key] = str(record["ticket"])
        elif record.get("kind") == "coverage":
            for req in ids_in_verdict(record, ns):
                ckey = (*key, req)
                best = coverage.get(ckey)
                verdict = str(record.get("verdict"))
                if best is None or _COVERAGE_RANK.get(verdict, 0) > _COVERAGE_RANK.get(best, 0):
                    coverage[ckey] = verdict

    by_req: dict[str, dict[str, set[str]]] = {}
    case_of: dict[str, str | None] = {}
    for link in link_list:
        for req in link.requirements:
            by_req.setdefault(req, {}).setdefault(link.nodeid, set()).add(link.origin)
        if link.case_id:
            case_of[link.nodeid] = link.case_id

    wanted = list(scope.requirements) or [r.id for r in registry.active()]
    rows: list[Row] = []
    for req_id in wanted:
        req = registry.get(req_id)
        cells = []
        for nodeid, origins in sorted(by_req.get(req_id, {}).items()):
            res = latest.get(nodeid)
            if res is None:
                cells.append(
                    Cell(
                        nodeid,
                        case_of.get(nodeid),
                        "not_run",
                        None,
                        None,
                        "not_run",
                        origins=tuple(sorted(origins)),
                    )
                )
                continue
            key = (res.run_id, nodeid)
            run_integrity = integrity.get((res.run_id, res.lane))
            detector = red.get(key)
            defects = tuple(dict.fromkeys(x for x in (res.defect, red_tickets.get(key)) if x))
            cells.append(
                Cell(
                    nodeid=nodeid,
                    case_id=res.case_id or case_of.get(nodeid),
                    outcome=res.outcome,
                    run_id=res.run_id,
                    run_integrity=run_integrity,
                    state=_cell_state(res.outcome, run_integrity, detector, res.failed_attempt),
                    coverage_verdict=coverage.get((res.run_id, nodeid, req_id)),
                    detector_verdict=detector,
                    defects=defects,
                    draft=res.review_state == "draft",
                    stale=res.stale,
                    origins=tuple(sorted(origins)),
                    first_failure=res.first_failure if res.failed_attempt else None,
                )
            )
        rows.append(
            Row(
                req_id,
                req.title if req else "",
                req.priority if req else None,
                _status(cells),
                cells,
            )
        )
    rows.sort(key=lambda r: (ORDER.index(r.status), r.requirement_id))
    unregistered = [
        {"requirement_id": req_id, "nodeids": sorted(nodes)}
        for req_id, nodes in sorted(by_req.items())
        if registry.status(req_id) == "unknown"
    ]
    return Matrix(
        scope=scope.to_json(),
        run_ids=list(dict.fromkeys(r.run_id for r in runs)),
        rows=rows,
        unregistered=unregistered,
        findings=coarse_links(registry, link_list),
        results_read=len(observed),
    )


def coarse_links(registry: Registry, links: Iterable[CaseLink]) -> list[dict[str, Any]]:
    """Each test linked to a container-level id (an epic): too coarse to be the requirement.

    Report only. A registry that records no ``kind`` produces no finding.
    """
    out = []
    for link in links:
        for req in link.requirements:
            entry = registry.get(req)
            if entry is not None and entry.kind in CONTAINER_KINDS:
                out.append(
                    {
                        "nodeid": link.nodeid,
                        "id": req,
                        "kind": entry.kind,
                        "finding": "link too coarse",
                    }
                )
    return sorted(
        {(f["nodeid"], f["id"]): f for f in out}.values(), key=lambda f: (f["id"], f["nodeid"])
    )


def matrix_json(matrix: Matrix) -> dict[str, Any]:
    data = matrix.to_json()
    validate(data, "matrix.schema.json")
    return data


def _md(text: object) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(m: Matrix) -> str:
    counts = m.counts()
    lines = [
        "## Requirements traceability",
        "",
        f"**{m.headline}**",
        "",
        " · ".join(f"{k} {v}" for k, v in counts.items() if v) or "no rows",
        "",
        f"Runs: {', '.join(m.run_ids) or 'none'}",
        "",
        "| Requirement | Title | Status | Tests | Notes |",
        "|---|---|---|---|---|",
    ]
    for row in m.rows:
        executed = [c for c in row.cells if c.state != "not_run"]
        passed = sum(1 for c in executed if c.state == "passed")
        tests = f"{passed}/{len(executed)} passed" if row.cells else "none linked"
        notes = []
        if row.stale:
            notes.append("stale: result from an older test revision")
        defects = sorted({d for c in row.cells for d in c.defects})
        if defects:
            notes.append("defects " + ", ".join(defects))
        if any(c.draft for c in row.cells):
            notes.append("includes draft tests")
        if any(c.state == "fix_observed" for c in row.cells):
            notes.append("detector cleared: verify, then remove the wrapper")
        for c in row.cells:
            if c.state == "flaky":
                first = c.first_failure or "no message recorded"
                notes.append(f"flaky: {c.nodeid.rsplit('::', 1)[-1]} first failed with {first}")
        lines.append(
            f"| {_md(row.requirement_id)} | {_md(row.title)} | {row.status.value} | {tests} "
            f"| {_md('; '.join(notes))} |"
        )
    if any(r.stale is None and r.cells for r in m.rows):
        lines += ["", "_Staleness not measured for some rows (no revision data in scope)._"]
    if m.unregistered:
        lines += ["", "Links to ids not in the registry:"]
        lines += [f"- {u['requirement_id']}: {len(u['nodeids'])} test(s)" for u in m.unregistered]
    if m.findings:
        lines += ["", "Links too coarse to verify a requirement (report only):"]
        lines += [f"- `{f['nodeid']}` links {f['id']} ({f['kind']})" for f in m.findings]
    if m.results_read == 0:
        lines += ["", "_No results were read: every status above is an absence of evidence._"]
    return "\n".join(lines) + "\n"


def render_csv(m: Matrix) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(m.csv_rows())
    return buffer.getvalue()
