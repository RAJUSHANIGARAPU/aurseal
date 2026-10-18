"""Run summary and server-side run trust.

The integrity verdict is computed here with the run gate's own rules
(:class:`gate.integrity.RunTrustGate`) over what the platform actually stored, so a
client cannot claim green by leaving results out. What the platform cannot judge, it
says: without a declared ``expected_count`` the "selection" rule is absent, and a run
that received fewer results than it declared is ``UNJUDGED`` (or ``FAIL`` when a gate
rule already breached), never ``PASS``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from gate.integrity import (
    Outcome,
    RunTrustGate,
    TestResult,
    Thresholds,
    Verdict,
    build_tally,
)
from gate.verdicts import VerdictRow
from qaplatform.schemas import SCHEMA_VERSION
from results._util import loads
from results.models import OUTCOMES

COVERAGE_VERDICTS = ("proved", "recorded", "held_empty", "abstained")
RED_VERDICTS = (
    "reproduces",
    "cleared",
    "held_empty",
    "signature_mismatch",
    "closed_pass",
    "closed_regressed",
)
SELECTION_ABSENT = (
    "selection - no expected_count was declared: the platform cannot tell a complete run "
    "from a partial one"
)


def api_status(store_status: str) -> str:
    return "open" if store_status == "running" else store_status


def judge(
    final_rows: Sequence[Mapping[str, Any]],
    verdict_rows: Sequence[Mapping[str, Any]],
    expected_count: int | None,
    thresholds: Thresholds | None = None,
) -> dict[str, Any]:
    """The integrity block of a run summary."""
    tests = [
        TestResult(
            nodeid=r["nodeid"],
            outcome=Outcome(r["outcome"]),
            markers=tuple(loads(r.get("markers_json"), []) or ()),
            reason=r.get("skip_reason") or "",
            defect=r.get("defect_ref"),
            cleared=bool(r.get("is_cleared")),
        )
        for r in final_rows
    ]
    rows = [
        VerdictRow(
            run_id=v.get("run_id"),
            nodeid=v["nodeid"],
            kind=v["kind"],
            ticket=v.get("ticket") or None,
            verdict=v["verdict"],
            detail=v.get("detail") or "",
        )
        for v in verdict_rows
    ]
    gate = RunTrustGate(thresholds or Thresholds.from_env())
    result = gate.evaluate(build_tally(tests, rows))
    breaches = [
        {"rule": b.rule, "detail": f"{b.observed}; required {b.required}"} for b in result.breaches
    ]
    absent = list(result.not_applicable)
    verdict = result.verdict
    received = len(tests)
    if expected_count is None:
        absent.append(SELECTION_ABSENT)
    elif received < expected_count:
        breaches.append(
            {
                "rule": "SELECTION",
                "detail": f"{received} of {expected_count} declared test(s) reported a result",
            }
        )
        if verdict is not Verdict.FAIL:
            verdict = Verdict.UNJUDGED
    return {
        "verdict": verdict.value,
        "assessed": verdict is not Verdict.UNJUDGED,
        "breaches": breaches,
        "rules_absent": absent,
    }


def tally(final_rows: Sequence[Mapping[str, Any]], expected_count: int | None) -> dict[str, Any]:
    counts: dict[str, Any] = {o: 0 for o in OUTCOMES}
    flaky = 0
    for r in final_rows:
        counts[r["outcome"]] += 1
        if r["outcome"] == "passed" and int(r.get("failed_attempts") or 0) > 0:
            flaky += 1
    return {"selected": expected_count, "received": len(final_rows), **counts, "flaky": flaky}


def verdict_counts(verdict_rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    out = {
        "coverage": {v: 0 for v in COVERAGE_VERDICTS},
        "expected_red": {v: 0 for v in RED_VERDICTS},
    }
    for v in verdict_rows:
        bucket = out.get(v["kind"])
        if bucket is not None:
            bucket[v["verdict"]] = bucket.get(v["verdict"], 0) + 1
    return out


def run_summary(
    project: str,
    run: Mapping[str, Any],
    final_rows: Sequence[Mapping[str, Any]],
    verdict_rows: Sequence[Mapping[str, Any]],
    *,
    integrity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A ``run-summary`` document. An open run is judged provisionally (what it has so far)."""
    expected = run.get("expected_count")
    integ = dict(integrity) if integrity is not None else judge(final_rows, verdict_rows, expected)
    if run.get("integrity_verdict") and integrity is None and run.get("status") != "running":
        integ["verdict"] = run["integrity_verdict"]
        integ["assessed"] = run["integrity_verdict"] != "UNJUDGED"
    run_id = run["run_id"]
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "external_id": run.get("external_id"),
        "project": project,
        "name": run.get("name"),
        "status": api_status(run.get("status") or "running"),
        "source": run.get("source"),
        "release": run.get("release_label"),
        "lane": run.get("lane"),
        "environment": run.get("environment"),
        "run_request_id": run.get("run_request_id"),
        "tally": tally(final_rows, expected),
        "verdicts": verdict_counts(verdict_rows),
        "integrity": integ,
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "links": {
            "self": f"/api/v1/projects/{project}/runs/{run_id}",
            "ui": f"/ui/{project}/runs/{run_id}",
        },
    }


__all__ = ["api_status", "judge", "run_summary", "tally", "verdict_counts"]
