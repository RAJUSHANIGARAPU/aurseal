"""Where traceability reads a run's results from: the run's own files, or the results store.

Both sources answer the same four questions (which runs, which results, which verdicts,
which links), so the matrix, plan evaluation and release verdict give the same answer
offline from ``reports/`` as they do from the database.

* :class:`ArtifactResultSource` reads ``run-trust*.json`` markers, ``verdicts*.jsonl``
  and ``case-links*.json`` anywhere under a reports directory (CI artefacts of several
  jobs can be downloaded side by side).
* :class:`StoreResultSource` reads the results store for an explicit scope; an unscoped
  request is refused, because "latest result" without a scope mixes branches.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from traceability.links import CaseLink, CaseLinks, load_all

EXECUTED = frozenset({"passed", "failed", "error", "xfailed"})
FAILING_OUTCOMES = frozenset({"failed", "error"})


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    lane: str = "default"
    integrity: str | None = None  # PASS | FAIL | UNJUDGED | None (no marker)
    started_at: str = ""
    release_label: str | None = None
    git_sha: str | None = None
    git_branch: str | None = None
    selected: int | None = None
    executed: int | None = None
    skipped: int | None = None

    @property
    def is_evidence(self) -> bool:
        """Only a run whose run-trust verdict is PASS is release evidence."""
        return self.integrity == "PASS"


@dataclass(frozen=True)
class ObservedResult:
    nodeid: str
    outcome: str
    run_id: str
    lane: str = "default"
    case_id: str | None = None
    defect: str | None = None
    markers: tuple[str, ...] = ()
    revision_id: str | None = None
    current_revision_id: str | None = None
    review_state: str = "approved"  # draft | approved

    @property
    def stale(self) -> bool | None:
        """True when the result came from an older revision of the test; None: unknown."""
        if self.revision_id is None or self.current_revision_id is None:
            return None
        return self.revision_id != self.current_revision_id


class ResultSource(Protocol):
    def runs(self) -> list[RunInfo]: ...

    def results(self) -> list[ObservedResult]: ...

    def verdicts(self) -> list[dict[str, Any]]: ...

    def links(self) -> CaseLinks: ...


@dataclass(frozen=True)
class Scope:
    """Which runs count. ``run_ids`` wins; else the newest ``last`` runs on ``branch``."""

    run_ids: tuple[str, ...] = ()
    branch: str | None = None
    lane: str | None = None
    last: int | None = None
    release: str | None = None
    requirements: tuple[str, ...] = ()  # restrict the matrix to these ids (a plan's)

    @property
    def is_scoped(self) -> bool:
        return bool(self.run_ids or self.branch or self.release)

    def to_json(self) -> dict[str, Any]:
        return {
            "run_ids": list(self.run_ids),
            "branch": self.branch,
            "lane": self.lane,
            "last": self.last,
            "release": self.release,
            "requirements": list(self.requirements),
        }


# --------------------------------------------------------------------------- artifacts


def _review_state(tests: Mapping[str, Mapping[str, Any]], nodeid: str, markers) -> str:
    entry = tests.get(nodeid) or {}
    if entry.get("review_state") == "draft" or "draft" in markers:
        return "draft"
    return "approved"


@dataclass
class ArtifactResultSource:
    reports_dir: Path
    run_ids: Sequence[str] = ()
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    def _markers(self) -> list[dict[str, Any]]:
        if "markers" not in self._cache:
            found = []
            for path in sorted(Path(self.reports_dir).rglob("run-trust*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict) or "run_id" not in data:
                    continue
                if self.run_ids and data["run_id"] not in self.run_ids:
                    continue
                found.append(data)
            found.sort(key=lambda d: str(d.get("timestamp") or ""))
            self._cache["markers"] = found
        return self._cache["markers"]

    def runs(self) -> list[RunInfo]:
        return [
            RunInfo(
                run_id=str(m["run_id"]),
                lane=str(m.get("lane") or "default"),
                integrity=m.get("verdict"),
                started_at=str(m.get("timestamp") or ""),
                selected=m.get("selected"),
                executed=m.get("executed"),
                skipped=m.get("skipped"),
            )
            for m in self._markers()
        ]

    def links(self) -> CaseLinks:
        if "links" not in self._cache:
            self._cache["links"] = load_all(self.reports_dir)
        return self._cache["links"]

    def results(self) -> list[ObservedResult]:
        tests_meta = self.links().tests
        case_ids = {link.nodeid: link.case_id for link in self.links().links if link.case_id}
        out = []
        for marker in self._markers():
            for nodeid, data in (marker.get("tests") or {}).items():
                markers = tuple(data.get("markers") or ())
                out.append(
                    ObservedResult(
                        nodeid=nodeid,
                        outcome=str(data.get("outcome") or "not_run"),
                        run_id=str(marker["run_id"]),
                        lane=str(marker.get("lane") or "default"),
                        case_id=case_ids.get(nodeid)
                        or (tests_meta.get(nodeid) or {}).get("case_id"),
                        defect=data.get("defect"),
                        markers=markers,
                        review_state=_review_state(tests_meta, nodeid, markers),
                    )
                )
        return out

    def verdicts(self) -> list[dict[str, Any]]:
        wanted = {r.run_id for r in self.runs()}
        out = []
        for path in sorted(Path(self.reports_dir).rglob("verdicts*.jsonl")):
            from verification.verdicts import read_verdicts

            out.extend(r for r in read_verdicts(path) if r.get("run_id") in wanted)
        return out


# --------------------------------------------------------------------------- store


class StoreResultSource:
    """The results store for an explicit :class:`Scope` (``ValueError`` when unscoped)."""

    def __init__(self, store: Any, scope: Scope, *, project_id: str = "default") -> None:
        if not scope.is_scoped:
            raise ValueError(
                "refusing an unscoped store query: name runs, a branch or a release "
                "(latest-anything across branches and environments misleads)"
            )
        self.store = store
        self.scope = scope
        self.project_id = project_id
        self._runs: list[RunInfo] | None = None

    def runs(self) -> list[RunInfo]:
        if self._runs is None:
            conn = self.store.conn
            params: list[Any] = [self.project_id]
            sql = "SELECT * FROM runs WHERE project_id = ?"
            if self.scope.run_ids:
                sql += f" AND run_id IN ({','.join('?' * len(self.scope.run_ids))})"
                params.extend(self.scope.run_ids)
            if self.scope.branch:
                sql += " AND git_branch = ?"
                params.append(self.scope.branch)
            if self.scope.release:
                sql += " AND release_label = ?"
                params.append(self.scope.release)
            if self.scope.lane:
                sql += " AND lane = ?"
                params.append(self.scope.lane)
            sql += " ORDER BY started_at DESC, id DESC"
            if self.scope.last and not self.scope.run_ids:
                sql += " LIMIT ?"
                params.append(int(self.scope.last))
            rows = [dict(r) for r in conn.execute(sql, params)]
            self._runs = [
                RunInfo(
                    run_id=r["run_id"],
                    lane=r.get("lane") or "default",
                    integrity=r.get("integrity_verdict"),
                    started_at=r.get("started_at") or "",
                    release_label=r.get("release_label"),
                    git_sha=r.get("git_sha"),
                    git_branch=r.get("git_branch"),
                )
                for r in reversed(rows)
            ]
        return self._runs

    def _run_ids(self) -> list[str]:
        return [r.run_id for r in self.runs()]

    def results(self) -> list[ObservedResult]:
        ids = self._run_ids()
        if not ids:
            return []
        lanes = {r.run_id: r.lane for r in self.runs()}
        rows = self.store.conn.execute(
            "SELECT f.run_id, f.nodeid, f.outcome, f.case_id, f.revision_id, f.defect_ref,"
            " f.markers_json, c.current_revision_id FROM final_results f"
            " LEFT JOIN cases c ON c.project_id = f.project_id AND c.case_id = f.case_id"
            f" WHERE f.run_id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        reviews = self._draft_cases()
        out = []
        for row in rows:
            markers = tuple(json.loads(row["markers_json"] or "[]"))
            draft = "draft" in markers or row["case_id"] in reviews
            out.append(
                ObservedResult(
                    nodeid=row["nodeid"],
                    outcome=row["outcome"],
                    run_id=row["run_id"],
                    lane=lanes.get(row["run_id"], "default"),
                    case_id=row["case_id"],
                    defect=row["defect_ref"],
                    markers=markers,
                    revision_id=row["revision_id"],
                    current_revision_id=row["current_revision_id"],
                    review_state="draft" if draft else "approved",
                )
            )
        return out

    def _draft_cases(self) -> set[str]:
        rows = self.store.conn.execute(
            "SELECT case_id FROM case_links WHERE project_id = ? AND kind = 'review'"
            " AND ref = 'draft'",
            (self.project_id,),
        )
        return {r[0] for r in rows}

    def verdicts(self) -> list[dict[str, Any]]:
        ids = self._run_ids()
        if not ids:
            return []
        rows = self.store.conn.execute(
            "SELECT run_id, nodeid, kind, ticket, verdict, detail, subject, recorded_at"
            f" FROM verdicts WHERE run_id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["ticket"] = data["ticket"] or None
            data["subject"] = data["subject"] or None
            data["timestamp"] = data.pop("recorded_at")
            out.append(data)
        return out

    def links(self) -> CaseLinks:
        by_case: dict[str, list[str]] = {}
        for case_id, ref in self.store.conn.execute(
            "SELECT case_id, ref FROM case_links WHERE project_id = ? AND kind = 'requirement'",
            (self.project_id,),
        ):
            by_case.setdefault(case_id, []).append(ref)
        doc = CaseLinks(run_id=",".join(self._run_ids()))
        nodeids: dict[str, set[str]] = {}
        for result in self.results():
            if result.case_id:
                nodeids.setdefault(result.case_id, set()).add(result.nodeid)
        for case_id, refs in sorted(by_case.items()):
            for nodeid in sorted(nodeids.get(case_id, ())) or [f"case:{case_id}"]:
                doc.links.append(CaseLink(nodeid, tuple(sorted(refs)), "marker", case_id))
        from traceability.requirements import ids_in_verdict

        seen: dict[str, list[str]] = {}
        for record in self.verdicts():
            if record.get("kind") == "coverage":
                for req in ids_in_verdict(record):
                    bucket = seen.setdefault(record["nodeid"], [])
                    if req not in bucket:
                        bucket.append(req)
        for nodeid, reqs in sorted(seen.items()):
            doc.links.append(CaseLink(nodeid, tuple(reqs), "coverage_verdict"))
        return doc


def latest_per_test(
    results: Iterable[ObservedResult], runs: Sequence[RunInfo]
) -> dict[str, ObservedResult]:
    """Per node id, the newest *executed* result in scope (else the newest of any kind).

    ``runs`` is ordered oldest first. A test the newest run skipped but an older run in
    scope executed keeps the older answer: the scope did execute it.
    """
    order = {(r.run_id, r.lane): i for i, r in enumerate(runs)}
    best: dict[str, ObservedResult] = {}
    for res in sorted(results, key=lambda r: order.get((r.run_id, r.lane), -1)):
        cur = best.get(res.nodeid)
        if cur is None or res.outcome in EXECUTED or cur.outcome not in EXECUTED:
            best[res.nodeid] = res
    return best
