"""Shared helpers for the release-verdict tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tests.traceability._runs import RunSpec, T, coverage, write_artifacts
from traceability.evidence import ArtifactResultSource
from traceability.release import Candidate, ReleasePolicy, decide_release
from traceability.requirements import Registry, Requirement

M = "tests/test_m.py::"
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
FRESH = "2026-05-31T12:00:00Z"  # a defect snapshot 12 hours before NOW
NOTHING_SHIPS = {"keys": [], "regressing": [], "not_compared": []}
PASSED_GAPS = {"ratchet": {"new": [], "closed": [], "passed": True}}
CANDIDATE = Candidate("R1", "rc1")
STRICT = {
    "version": 1,
    "default_priority": "must",
    "requirements": {
        "must": {
            "block": ["FAILING", "KNOWN_DEFECT"],
            "no_verdict": ["UNCOVERED", "NOT_RUN", "UNVERIFIED", "DRAFT_ONLY", "PASSING"],
        },
        "should": {"block": ["FAILING"], "no_verdict": ["UNCOVERED", "NOT_RUN", "UNVERIFIED"]},
    },
    "detectors": {
        "block": ["signature_mismatch", "closed_regressed"],
        "no_verdict": ["held_empty"],
        "attention": ["cleared"],
    },
}


def policy(**overrides) -> ReleasePolicy:
    return ReleasePolicy.from_body({**STRICT, **overrides}, "test")


def reg(*items: tuple[str, str]) -> Registry:
    return Registry.from_requirements(Requirement(i, i, priority=p) for i, p in items)


def proved_run(run_id: str = "r1", **kw) -> RunSpec:
    return RunSpec(
        run_id, [T(M + "a", reqs=("REQ-1",))], verdicts=[coverage(run_id, M + "a", "REQ-1 x")], **kw
    )


def decide(tmp_path: Path, runs, registry=None, pol=None, gaps=PASSED_GAPS, **kw):
    """The verdict with every other input present and clean: a delta that ships nothing and
    a passing gap ratchet, unless the test overrides them."""
    reports = write_artifacts(tmp_path / "reports", runs)
    if gaps is not None:
        (reports / "traceability").mkdir(parents=True, exist_ok=True)
        (reports / "traceability" / "gaps.json").write_text(json.dumps(gaps))
    kw.setdefault("delta", NOTHING_SHIPS)
    return decide_release(
        CANDIDATE,
        ArtifactResultSource(reports),
        pol or policy(),
        registry=registry or reg(("REQ-1", "must")),
        now=NOW,
        **kw,
    )
