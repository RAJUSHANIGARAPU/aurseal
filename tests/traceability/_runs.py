"""Build the same run twice: as CI artefacts on disk and as rows in a results store."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from results.catalogue import case_id_for_nodeid, case_key_from_nodeid
from results.models import CaseRecord, ResultRecord, RunRecord, VerdictRecord
from traceability.links import CaseLink, CaseLinks, links_path, write_case_links
from traceability.requirements import Registry, Requirement


@dataclass
class T:
    nodeid: str
    outcome: str = "passed"
    reqs: tuple[str, ...] = ()
    draft: bool = False
    defect: str | None = None
    failed_first: str | None = None  # an earlier attempt in the same run failed with this


@dataclass
class RunSpec:
    run_id: str
    tests: list[T]
    integrity: str = "PASS"
    lane: str = "default"
    verdicts: list[dict] = field(default_factory=list)
    timestamp: str = "2026-01-01T00:00:00+00:00"
    release: str | None = None
    branch: str | None = "main"


def coverage(run_id: str, nodeid: str, subject: str, verdict: str = "proved") -> dict:
    return {
        "run_id": run_id,
        "nodeid": nodeid,
        "kind": "coverage",
        "ticket": None,
        "verdict": verdict,
        "detail": "observed 3 of 3",
        "subject": subject,
        "timestamp": "2026-01-01T00:00:01+00:00",
    }


def red(run_id: str, nodeid: str, ticket: str, verdict: str = "reproduces") -> dict:
    return {
        "run_id": run_id,
        "nodeid": nodeid,
        "kind": "expected_red",
        "ticket": ticket,
        "verdict": verdict,
        "detail": "totals differ by one cent",
        "subject": ticket,
        "timestamp": "2026-01-01T00:00:01+00:00",
    }


def registry(*ids: str, priority: str = "must") -> Registry:
    return Registry.from_requirements(Requirement(i, f"title {i}", priority=priority) for i in ids)


def write_artifacts(root: Path, runs: list[RunSpec]) -> Path:
    for n, run in enumerate(runs):
        job = root / f"job{n}"
        suffix = "" if run.lane == "default" else f"-{run.lane}"
        marker = {
            "verdict": run.integrity,
            "run_id": run.run_id,
            "lane": run.lane,
            "timestamp": run.timestamp,
            "selected": len(run.tests),
            "executed": sum(
                t.outcome in ("passed", "failed", "error", "xfailed") for t in run.tests
            ),
            "skipped": sum(t.outcome == "skipped" for t in run.tests),
            "tests": {
                t.nodeid: {
                    "outcome": t.outcome,
                    "markers": ["draft"] if t.draft else [],
                    "defect": t.defect,
                    "failed_attempts": 1 if t.failed_first is not None else 0,
                    "first_failure": t.failed_first or "",
                }
                for t in run.tests
            },
        }
        job.mkdir(parents=True, exist_ok=True)
        (job / f"run-trust{suffix}.json").write_text(json.dumps(marker))
        (job / "verdicts").mkdir(exist_ok=True)
        (job / "verdicts" / "verdicts.jsonl").write_text(
            "".join(json.dumps(v) + "\n" for v in run.verdicts)
        )
        doc = CaseLinks(run_id=run.run_id)
        for t in run.tests:
            if t.reqs:
                doc.links.append(CaseLink(t.nodeid, t.reqs, "marker", case_id_for_nodeid(t.nodeid)))
            if t.draft:
                doc.tests[t.nodeid] = {"review_state": "draft"}
        write_case_links(doc, links_path(job / "traceability", run.lane))
    return root


def load_store(store, runs: list[RunSpec]) -> None:
    for run in runs:
        store.begin_run(
            RunRecord(
                run.run_id,
                lane=run.lane,
                started_at=run.timestamp,
                integrity_verdict=run.integrity,
                git_branch=run.branch,
                release_label=run.release,
                status="finished",
            )
        )
        store.upsert_cases(
            CaseRecord(case_id_for_nodeid(t.nodeid), case_key_from_nodeid(t.nodeid))
            for t in run.tests
        )
        store.add_links(
            [(case_id_for_nodeid(t.nodeid), "requirement", r) for t in run.tests for r in t.reqs],
            source="marker",
        )
        store.add_results(
            ResultRecord(
                run.run_id,
                t.nodeid,
                "failed",
                attempt=1,
                case_id=case_id_for_nodeid(t.nodeid),
                message=t.failed_first,
            )
            for t in run.tests
            if t.failed_first is not None
        )
        store.add_results(
            ResultRecord(
                run.run_id,
                t.nodeid,
                t.outcome,
                attempt=2 if t.failed_first is not None else 1,
                case_id=case_id_for_nodeid(t.nodeid),
                defect_ref=t.defect,
                markers=("draft",) if t.draft else (),
            )
            for t in run.tests
        )
        store.add_verdicts(VerdictRecord.from_row(v) for v in run.verdicts)
