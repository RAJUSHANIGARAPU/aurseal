"""Read models the API exposes but does not own: traceability and the release verdict.

Both are pluggable. The default traceability reader below derives the matrix from what the
results store already holds (requirement links on cases, final results, ``coverage``
verdict rows), so the endpoint works on day one. A dedicated traceability engine can
replace it by implementing :class:`TraceabilityReader`. The release verdict is only served
when a release policy is configured (:func:`release_verdicts_from_env`): it is then
:func:`traceability.release.decide_release` over the project's runs for that release,
mapped onto the platform's ``release-verdict`` shape by :class:`PolicyReleaseVerdicts`.
Without a policy the server answers "not configured" rather than guessing.

Status per requirement, strongest evidence first:

* ``failing`` — a linked test's latest final outcome is failed or error;
* ``proved`` — a ``coverage`` verdict ``proved`` is the latest verdict for the requirement
  from a test whose latest outcome is passed (only ``proved`` counts as evidence);
* ``recorded_only`` — tests ran and passed, or verdicts were only ``recorded``;
* ``held_empty`` — linked tests exist but none executed;
* ``no_tests`` — the requirement was asked for and nothing links to it.
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from qaplatform.errors import NotConfigured
from qaplatform.schemas import SCHEMA_VERSION
from results._util import utc_now
from results.catalogue import case_id_for_nodeid

EXECUTED = {"passed", "failed", "error", "xfailed"}
POLICY_ENV = "QAP_RELEASE_POLICY"
WAIVERS_ENV = "QAP_RELEASE_WAIVERS"
DECISIONS = {"GO": "go", "GO_WITH_WAIVERS": "go", "NO_GO": "no_go", "NO_VERDICT": "not_assessed"}
RESULTS = {"block": "breached", "no_verdict": "absent"}  # attention, info, waived: met


class TraceabilityReader(Protocol):
    def traceability(
        self, scoped: Any, *, requirement: str | None, release: str | None
    ) -> dict[str, Any]: ...


class ReleaseVerdictReader(Protocol):
    def release_verdict(self, scoped: Any, release: str) -> dict[str, Any]: ...


class StoreTraceability:
    """The default reader: requirement links + final results + coverage verdicts."""

    def traceability(
        self, scoped: Any, *, requirement: str | None = None, release: str | None = None
    ) -> dict[str, Any]:
        latest: dict[str, dict[str, Any]] = {}
        for row in scoped.latest_results(release):
            latest[row["case_id"]] = row
        verdict_by: dict[tuple[str, str], dict[str, Any]] = {}
        verdict_reqs: dict[str, set[str]] = defaultdict(set)
        for v in scoped.coverage_verdicts(release):
            if not v.get("subject"):
                continue
            verdict_by[(v["subject"], v["nodeid"])] = v
            verdict_reqs[v["subject"]].add(v["nodeid"])
        tests_by_req: dict[str, dict[str, str]] = defaultdict(dict)  # req -> {test_id: case_id}
        for link in scoped.requirement_links(requirement):
            row = latest.get(link["case_id"])
            test_id = row["nodeid"] if row else link["case_key"]
            tests_by_req[link["requirement"]][test_id] = link["case_id"]
        for req, nodeids in verdict_reqs.items():
            if requirement is None or req == requirement:
                for nodeid in nodeids:
                    tests_by_req[req].setdefault(nodeid, case_id_for_nodeid(nodeid))
        tickets_by_case: dict[str, list[str]] = defaultdict(list)
        for link in scoped.defect_links():
            tickets_by_case[link["case_id"]].append(link["ticket"])
        reqs = sorted(tests_by_req) if requirement is None else [requirement]
        rows = []
        for req in reqs:
            tests = []
            tickets: set[str] = set()
            for test_id, case_id in sorted(tests_by_req.get(req, {}).items()):
                result = latest.get(case_id)
                verdict = verdict_by.get((req, test_id))
                tickets.update(tickets_by_case.get(case_id, ()))
                tests.append(
                    {
                        "test_id": test_id,
                        "latest_outcome": result["outcome"] if result else None,
                        "latest_verdict": verdict["verdict"] if verdict else None,
                        "run_id": (result or verdict or {}).get("run_id"),
                        "source": (result or verdict or {}).get("source"),
                        "at": (result or {}).get("started_at")
                        or (verdict or {}).get("recorded_at"),
                    }
                )
            rows.append(
                {
                    "requirement": req,
                    "status": requirement_status(tests),
                    "tests": tests,
                    "open_tickets": sorted(tickets),
                }
            )
        return {
            "schema_version": SCHEMA_VERSION,
            "project": scoped.project_id,
            "release": release,
            "generated_at": utc_now(),
            "rows": rows,
        }


def requirement_status(tests: list[dict[str, Any]]) -> str:
    if not tests:
        return "no_tests"
    outcomes = [t["latest_outcome"] for t in tests]
    if any(o in ("failed", "error") for o in outcomes):
        return "failing"
    if any(
        t["latest_verdict"] == "proved" and t["latest_outcome"] in (None, "passed") for t in tests
    ):
        return "proved"
    if any(o in EXECUTED for o in outcomes) or any(
        t["latest_verdict"] == "recorded" for t in tests
    ):
        return "recorded_only"
    return "held_empty"


class PolicyReleaseVerdicts:
    """The release verdict from the release policy, the project's runs and the defect snapshot.

    Evidence is every run labelled with the release; only runs with run-trust PASS count
    (``decide_release`` lists the others and ignores them). The full decision is kept under
    ``evidence``; ``reasons`` carry every criterion, so a waived or attention item is visible
    even though it does not change the decision.
    """

    def __init__(
        self,
        policy: str | Path,
        waivers: str | Path | None = None,
        *,
        open_defects: Callable[[], list[Mapping[str, Any]]] | None = None,
    ) -> None:
        self.policy = Path(policy)
        self.waivers = Path(waivers) if waivers else None
        self.open_defects = open_defects

    def release_verdict(self, scoped: Any, release: str) -> dict[str, Any]:
        from traceability.release import decide_release, load_policy, load_waivers

        if not self.policy.is_file():
            raise NotConfigured(f"release policy {self.policy} does not exist (set {POLICY_ENV})")
        candidate = _candidate(release)
        decision = decide_release(
            candidate,
            scoped.release_evidence(release),
            load_policy(self.policy),
            registry=scoped.requirement_registry(),
            waivers=load_waivers(self.waivers) if self.waivers else (),
            open_defects=self.open_defects or _snapshot_defects(),
        )
        doc = decision.to_json()
        return {
            "schema_version": SCHEMA_VERSION,
            "project": scoped.project_id,
            "release": release,
            "decision": DECISIONS[doc["verdict"]],
            "reasons": [_reason(r) for r in doc["reasons"]],
            "evidence": {
                k: doc[k]
                for k in ("verdict", "evidence_runs", "inputs_hash", "policy_revision", "waivers")
            },
            "evaluated_at": doc["computed_at"],
        }


def _candidate(release: str) -> Any:
    from traceability.release import Candidate

    return Candidate(release_id=release, candidate_id=release)


def _snapshot_defects() -> Callable[[], list[Mapping[str, Any]]]:
    from traceability.defects import open_defects_from_snapshot

    return open_defects_from_snapshot()


def _reason(reason: Mapping[str, Any]) -> dict[str, Any]:
    detail = f"{reason['severity']}: {reason['message']} (exhibit: {reason['exhibit']})"
    waiver = reason.get("waiver")
    if waiver:
        detail += f"; waived by {waiver['owner']} until {waiver['expires']}: {waiver['reason']}"
    return {
        "rule": reason["criterion"],
        "result": RESULTS.get(reason["severity"], "met"),
        "detail": detail,
    }


def release_verdicts_from_env(
    environ: Mapping[str, str] | None = None, *, cwd: str | Path | None = None
) -> PolicyReleaseVerdicts | None:
    """The configured release-verdict reader, or ``None`` when no policy is configured.

    ``QAP_RELEASE_POLICY`` names the policy file; without it a ``release-policy.yaml`` in
    the working directory is used when there is one. ``QAP_RELEASE_WAIVERS`` (default
    ``release-waivers.yaml`` beside the working directory) is optional.
    """
    from traceability.release import DEFAULT_POLICY, DEFAULT_WAIVERS

    env = os.environ if environ is None else environ
    base = Path(cwd) if cwd is not None else Path.cwd()
    raw = env.get(POLICY_ENV, "").strip()
    if raw:
        policy = Path(raw)
    elif (base / DEFAULT_POLICY).is_file():
        policy = base / DEFAULT_POLICY
    else:
        return None
    waivers = env.get(WAIVERS_ENV, "").strip() or base / DEFAULT_WAIVERS
    return PolicyReleaseVerdicts(policy, waivers)


__all__ = [
    "PolicyReleaseVerdicts",
    "ReleaseVerdictReader",
    "StoreTraceability",
    "TraceabilityReader",
    "release_verdicts_from_env",
    "requirement_status",
]
