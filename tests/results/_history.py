"""Synthetic run history for the trend, dashboard and search tests."""

from __future__ import annotations

from results.models import CaseRecord, ResultRecord, RunRecord


def add_run(
    store,
    run_id: str,
    day: int,
    outcomes: dict[str, str | list[str]],
    *,
    lane: str = "offline",
    branch: str = "main",
    verdict: str | None = "PASS",
    durations: dict[str, int] | None = None,
    revisions: dict[str, str] | None = None,
    fingerprints: dict[str, str] | None = None,
    messages: dict[str, str] | None = None,
) -> None:
    """One finished run; an outcome list is the attempts of one test, last one final."""
    store.begin_run(
        RunRecord(
            run_id=run_id,
            lane=lane,
            git_branch=branch,
            started_at=f"2026-01-{day:02d}T10:00:00.000Z",
        )
    )
    seq = store.begin_invocation(run_id, [], 1)
    cases, revs, rows = [], [], []
    for name, outcome in outcomes.items():
        case_id = f"c-{name}"
        cases.append(CaseRecord(case_id, f"tests/test_x.py::{name}", title=name.replace("_", " ")))
        rev = (revisions or {}).get(name)
        if rev:
            from results.models import RevisionRecord

            revs.append(RevisionRecord(rev, case_id, first_seen_run_id=run_id))
        attempts = outcome if isinstance(outcome, list) else [outcome]
        for i, out in enumerate(attempts, 1):
            rows.append(
                ResultRecord(
                    run_id,
                    f"tests/test_x.py::{name}",
                    out,
                    i,
                    invocation=seq,
                    case_id=case_id,
                    revision_id=rev,
                    duration_ms=(durations or {}).get(name, 100),
                    failure_fingerprint=(fingerprints or {}).get(name)
                    if out in ("failed", "error")
                    else None,
                    message=(messages or {}).get(name) if out in ("failed", "error") else None,
                )
            )
    store.upsert_cases(cases)
    if revs:
        store.add_revisions(revs)
    store.add_results(rows)
    store.finish_run(run_id, "finished", verdict, finished_at=f"2026-01-{day:02d}T10:05:00.000Z")
