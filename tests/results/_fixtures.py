"""A small, fully populated run used by the bundle, API and export tests."""

from __future__ import annotations

from results.models import (
    CaseRecord,
    EvidenceRecord,
    ProvenanceRecord,
    ResultRecord,
    RevisionRecord,
    RunRecord,
    VerdictRecord,
)


def populate(store, run_id: str = "r1") -> None:
    store.begin_run(
        RunRecord(
            run_id=run_id,
            lane="offline",
            git_branch="main",
            release_label="2026.10",
            expected_count=3,
            provenance={"repository": "example"},
        )
    )
    seq = store.begin_invocation(run_id, ["tests/", "-n", "2"], 2)
    store.upsert_cases(
        [
            CaseRecord(
                "c-a",
                "tests/test_x.py::test_a",
                title="A works",
                owner="team-a",
                priority="P1",
                markers=({"name": "requirement", "args": ["REQ-12"]},),
                links=(("requirement", "REQ-12"),),
            ),
            CaseRecord("c-b", "tests/test_x.py::test_b", title="B works"),
        ]
    )
    store.add_revisions([RevisionRecord("rev-a", "c-a", steps=("open", "check"))])
    store.add_results(
        [
            ResultRecord(
                run_id,
                "tests/test_x.py::test_a",
                "failed",
                1,
                invocation=seq,
                case_id="c-a",
                revision_id="rev-a",
                message="boom",
                worker="gw0",
                failure_fingerprint="f1",
                duration_ms=12,
            ),
            ResultRecord(
                run_id,
                "tests/test_x.py::test_a",
                "passed",
                2,
                invocation=seq,
                case_id="c-a",
                revision_id="rev-a",
                worker="gw1",
                duration_ms=10,
                markers=("smoke",),
            ),
            ResultRecord(
                run_id,
                "tests/test_x.py::test_b",
                "skipped",
                1,
                invocation=seq,
                case_id="c-b",
                skip_reason="no env",
                message="no env",
            ),
            ResultRecord(
                run_id, "tests/test_x.py::test_c[1]", "not_run", 1, invocation=seq, param_id="1"
            ),
        ]
    )
    store.add_evidence(
        [
            EvidenceRecord(
                run_id,
                "trace",
                "traces/a.zip",
                nodeid="tests/test_x.py::test_a",
                sha256="0" * 64,
                bytes=10,
            ),
            EvidenceRecord(run_id, "allure", "reports/allure-results"),
        ]
    )
    store.add_verdicts(
        [
            VerdictRecord(
                run_id,
                "tests/test_x.py::test_a",
                "coverage",
                "proved",
                subject="REQ-12",
                recorded_at="2026-01-01T00:00:00Z",
            ),
            VerdictRecord(
                run_id,
                "tests/test_x.py::test_b",
                "expected_red",
                "reproduces",
                ticket="BUG-142",
                detail="still broken",
            ),
        ]
    )
    store.add_provenance(
        [
            ProvenanceRecord(
                run_id,
                "tests/test_x.py::test_a",
                "fresh",
                "order",
                "ORD-42",
                attributes={"k": 1},
                recorded_at="2026-01-01T00:00:00Z",
            )
        ]
    )
    store.finish_invocation(run_id, seq, 1, 2)
    store.finish_run(run_id, "finished", "PASS")
