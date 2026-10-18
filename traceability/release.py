"""Release quality verdict: GO, GO_WITH_WAIVERS, NO_GO or NO_VERDICT, every reason an exhibit.

The verdict summarises evidence; it does not invent any. Composition, in order:

1. **Run trust.** Only runs whose run-trust verdict is PASS are evidence. No such
   run: NO_VERDICT ("no run is evidence"), never GO. Other runs are listed and ignored.
2. **Detector verdicts** over the evidence runs (worst per defect): ``signature_mismatch``
   or ``closed_regressed`` block; ``held_empty`` is no verdict for that defect;
   ``cleared`` is attention (the defect looks fixed: close it, unwrap the detector).
3. **Requirement coverage** from the matrix (scoped to the plan when there is one). The
   policy says, per requirement priority, which statuses block and which are no verdict.
   The default is strict: for a must-have, anything short of PROVED is no verdict.
4. **Open defects** from the tracker: at or above a blocking severity or label they block;
   a tracker that cannot be read is no verdict, never "zero open".
5. **Plan exit criteria**, folded in as they are: ``unmet`` blocks, ``no_evidence`` is no
   verdict.

A **waiver** (criterion, owner, reason, expiry) turns one blocking or no-verdict reason
into a waived one; the result is GO_WITH_WAIVERS and every waiver is printed. An expired
waiver is ignored (and said so). The decision carries a hash of all its inputs: the same
inputs always give the same answer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from traceability._util import load_yaml, utc_now, validate
from traceability.evidence import ObservedResult, ResultSource, RunInfo, Scope
from traceability.links import CaseLinks
from traceability.matrix import Matrix, build_matrix
from traceability.requirements import Registry
from verification.status_report import aggregate_defects

DEFAULT_POLICY = Path("release-policy.yaml")
DEFAULT_WAIVERS = Path("release-waivers.yaml")
KNOWN_RED = {
    "reproduces",
    "cleared",
    "held_empty",
    "signature_mismatch",
    "closed_pass",
    "closed_regressed",
}

BLOCK, NO_VERDICT, ATTENTION, INFO, WAIVED = "block", "no_verdict", "attention", "info", "waived"


class ReleaseVerdict(str, Enum):
    GO = "GO"
    GO_WITH_WAIVERS = "GO_WITH_WAIVERS"
    NO_GO = "NO_GO"
    NO_VERDICT = "NO_VERDICT"


EXIT_CODES = {
    ReleaseVerdict.GO: 0,
    ReleaseVerdict.GO_WITH_WAIVERS: 0,
    ReleaseVerdict.NO_GO: 1,
    ReleaseVerdict.NO_VERDICT: 3,
}


@dataclass(frozen=True)
class Candidate:
    release_id: str
    candidate_id: str
    version: str | None = None
    commit: str | None = None
    plan_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "candidate_id": self.candidate_id,
            "version": self.version,
            "commit": self.commit,
            "plan_id": self.plan_id,
        }


@dataclass(frozen=True)
class Reason:
    kind: str  # integrity | detector | requirement | defect | plan | waiver | policy
    severity: str  # block | no_verdict | attention | info | waived
    criterion: str  # what a waiver names, e.g. "requirement:REQ-4"
    exhibit: str  # where to look: run id + marker, verdict row, matrix row, defect id
    message: str
    waiver: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        data = {
            "kind": self.kind,
            "severity": self.severity,
            "criterion": self.criterion,
            "exhibit": self.exhibit,
            "message": self.message,
        }
        if self.waiver is not None:
            data["waiver"] = self.waiver
        return data


@dataclass(frozen=True)
class Waiver:
    criterion: str
    owner: str
    reason: str
    expires: date
    release: str | None = None  # None: any release

    def applies(self, release_id: str, today: date) -> bool:
        return (self.release in (None, release_id)) and today <= self.expires

    def to_json(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "owner": self.owner,
            "reason": self.reason,
            "expires": self.expires.isoformat(),
            "release": self.release,
        }


@dataclass(frozen=True)
class RequirementRule:
    block: frozenset[str]
    no_verdict: frozenset[str]


@dataclass(frozen=True)
class ReleasePolicy:
    body: dict[str, Any]
    revision: str
    requirements: dict[str, RequirementRule]
    default_priority: str
    detector_block: frozenset[str]
    detector_no_verdict: frozenset[str]
    detector_attention: frozenset[str]
    defect_severities: frozenset[str]
    defect_labels: frozenset[str]
    check_defects: bool

    @classmethod
    def from_body(cls, body: Mapping[str, Any], revision: str = "") -> ReleasePolicy:
        validate(dict(body), "release-policy.schema.json")
        reqs = {
            prio: RequirementRule(
                frozenset(rule.get("block") or ()), frozenset(rule.get("no_verdict") or ())
            )
            for prio, rule in (body.get("requirements") or {}).items()
        }
        det = body.get("detectors") or {}
        defects = body.get("defects")
        return cls(
            body=dict(body),
            revision=revision,
            requirements=reqs,
            default_priority=str(body.get("default_priority") or "must"),
            detector_block=frozenset(det.get("block") or ()),
            detector_no_verdict=frozenset(det.get("no_verdict") or ()),
            detector_attention=frozenset(det.get("attention") or ()),
            defect_severities=frozenset((defects or {}).get("blocking_severities") or ()),
            defect_labels=frozenset((defects or {}).get("blocking_labels") or ()),
            check_defects=defects is not None,
        )


def load_policy(path: str | Path = DEFAULT_POLICY) -> ReleasePolicy:
    raw = Path(path).read_bytes()
    body = load_yaml(Path(path)) or {}
    if not isinstance(body, dict):
        raise ValueError(f"{path}: a release policy is a mapping")
    return ReleasePolicy.from_body(body, hashlib.sha256(raw).hexdigest()[:16])


def load_waivers(path: str | Path = DEFAULT_WAIVERS) -> list[Waiver]:
    target = Path(path)
    if not target.is_file():
        return []
    body = load_yaml(target) or {"waivers": []}
    validate(body, "release-waivers.schema.json")
    out = []
    for item in body.get("waivers") or ():
        expires = item["expires"]
        if not isinstance(expires, date):
            expires = date.fromisoformat(str(expires))
        out.append(
            Waiver(
                criterion=str(item["criterion"]),
                owner=str(item["owner"]),
                reason=str(item["reason"]),
                expires=expires,
                release=None if item.get("release") is None else str(item["release"]),
            )
        )
    return out


# ---------------------------------------------------------------------- evidence view


class EvidenceView:
    """A result source limited to runs whose run-trust verdict is PASS."""

    def __init__(self, source: ResultSource) -> None:
        self.source = source
        self._runs = [r for r in source.runs() if r.is_evidence]
        self._keys = {(r.run_id, r.lane) for r in self._runs}

    def runs(self) -> list[RunInfo]:
        return list(self._runs)

    def results(self) -> list[ObservedResult]:
        return [r for r in self.source.results() if (r.run_id, r.lane) in self._keys]

    def verdicts(self) -> list[dict[str, Any]]:
        ids = {r.run_id for r in self._runs}
        return [v for v in self.source.verdicts() if v.get("run_id") in ids]

    def links(self) -> CaseLinks:
        return self.source.links()


# ---------------------------------------------------------------------- decision


@dataclass
class ReleaseDecision:
    candidate: Candidate
    verdict: ReleaseVerdict
    reasons: list[Reason]
    inputs_hash: str
    policy_revision: str
    evidence_runs: list[str]
    matrix: dict[str, Any] | None = None
    computed_at: str = field(default_factory=utc_now)

    @property
    def waivers(self) -> list[dict[str, Any]]:
        return [r.waiver for r in self.reasons if r.waiver is not None]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "candidate": self.candidate.to_json(),
            "verdict": self.verdict.value,
            "computed_at": self.computed_at,
            "inputs_hash": self.inputs_hash,
            "policy_revision": self.policy_revision,
            "evidence_runs": self.evidence_runs,
            "reasons": [r.to_json() for r in self.reasons],
            "waivers": self.waivers,
        }

    def to_markdown(self) -> str:
        c = self.candidate
        lines = [
            f"## Release {c.release_id} / {c.candidate_id}: {self.verdict.value}",
            "",
            f"Evidence runs: {', '.join(self.evidence_runs) or 'none'} · "
            f"policy `{self.policy_revision}` · inputs `{self.inputs_hash[:16]}`",
            "",
        ]
        for severity, title in (
            (BLOCK, "Blocking"),
            (NO_VERDICT, "No verdict"),
            (WAIVED, "Waived"),
            (ATTENTION, "Attention"),
            (INFO, "Information"),
        ):
            group = [r for r in self.reasons if r.severity == severity]
            if not group:
                continue
            lines += [f"### {title} ({len(group)})", ""]
            for r in group:
                line = f"- **{r.criterion}**: {r.message} (exhibit: {r.exhibit})"
                if r.waiver:
                    w = r.waiver
                    line += f" — waived by {w['owner']} until {w['expires']}: {w['reason']}"
                lines.append(line)
            lines.append("")
        return "\n".join(lines)


def _requirement_reasons(matrix: Matrix, policy: ReleasePolicy) -> list[Reason]:
    out = []
    for row in matrix.rows:
        prio = row.priority or policy.default_priority
        rule = policy.requirements.get(prio)
        criterion = f"requirement:{row.requirement_id}"
        exhibit = f"matrix row {row.requirement_id}"
        status = row.status.value
        if rule is None:
            out.append(
                Reason(
                    "policy",
                    NO_VERDICT,
                    criterion,
                    exhibit,
                    f"no policy rule for priority {prio!r}",
                )
            )
        elif status in rule.block:
            out.append(
                Reason("requirement", BLOCK, criterion, exhibit, f"{prio} requirement is {status}")
            )
        elif status in rule.no_verdict:
            out.append(
                Reason(
                    "requirement",
                    NO_VERDICT,
                    criterion,
                    exhibit,
                    f"{prio} requirement is {status}: not enough evidence",
                )
            )
        if row.stale:
            out.append(
                Reason(
                    "requirement",
                    ATTENTION,
                    criterion,
                    exhibit,
                    "result comes from an older revision of the test",
                )
            )
    return out


def _detector_reasons(verdicts: Iterable[Mapping[str, Any]], policy: ReleasePolicy) -> list[Reason]:
    rows = list(verdicts)
    out = []
    for record in rows:
        if record.get("kind") not in ("expected_red", "coverage"):
            out.append(
                Reason(
                    "detector",
                    ATTENTION,
                    f"verdict-kind:{record.get('kind')}",
                    f"verdict row {record.get('nodeid')}",
                    "unknown verdict kind seen; the release verdict cannot judge it",
                )
            )
        elif record.get("kind") == "expected_red" and record.get("verdict") not in KNOWN_RED:
            out.append(
                Reason(
                    "detector",
                    ATTENTION,
                    f"detector:{record.get('ticket')}",
                    f"verdict row {record.get('nodeid')}",
                    f"unknown detector verdict {record.get('verdict')!r}",
                )
            )
    for ticket, agg in aggregate_defects(rows).items():
        criterion = f"detector:{ticket}"
        exhibit = f"verdicts for {ticket} ({agg.records} row(s))"
        if agg.verdict in policy.detector_block:
            out.append(
                Reason("detector", BLOCK, criterion, exhibit, f"{agg.verdict}: {agg.detail}")
            )
        elif agg.verdict in policy.detector_no_verdict:
            out.append(
                Reason(
                    "detector",
                    NO_VERDICT,
                    criterion,
                    exhibit,
                    f"{agg.verdict}: the detector could not tell",
                )
            )
        elif agg.verdict in policy.detector_attention:
            out.append(
                Reason(
                    "detector",
                    ATTENTION,
                    criterion,
                    exhibit,
                    f"{agg.verdict}: looks fixed; verify, close it, unwrap the detector",
                )
            )
    return out


def _defect_reasons(
    open_defects: Callable[[], list[Mapping[str, Any]]] | None, policy: ReleasePolicy
) -> tuple[list[Reason], list[dict[str, Any]]]:
    if not policy.check_defects:
        return [], []
    if open_defects is None:
        return [
            Reason(
                "defect",
                NO_VERDICT,
                "defects",
                "tracker",
                "the policy checks open defects but no tracker is configured",
            )
        ], []
    try:
        defects = [dict(d) for d in open_defects()]
    except Exception as exc:  # an unreadable tracker is no evidence, never "zero open"
        return [
            Reason("defect", NO_VERDICT, "defects", "tracker", f"defect tracker unreachable: {exc}")
        ], []
    out = []
    for d in sorted(defects, key=lambda x: str(x.get("id"))):
        labels = set(d.get("labels") or ())
        severity = d.get("severity")
        if (severity in policy.defect_severities) or (labels & policy.defect_labels):
            why = severity or ", ".join(sorted(labels))
            title = f": {d['title']}" if d.get("title") else ""
            out.append(
                Reason(
                    "defect",
                    BLOCK,
                    f"defect:{d.get('id')}",
                    str(d.get("id")),
                    f"open defect {d.get('id')} ({why}){title}",
                )
            )
    return out, defects


def _plan_reasons(evaluation: Any | None) -> list[Reason]:
    if evaluation is None:
        return []
    out = []
    for c in evaluation.criteria:
        criterion = f"plan:{c.name}"
        exhibit = f"plan {evaluation.plan_id} @ {evaluation.plan_revision[:12]}"
        if c.result == "unmet":
            out.append(Reason("plan", BLOCK, criterion, exhibit, c.detail))
        elif c.result == "no_evidence":
            out.append(Reason("plan", NO_VERDICT, criterion, exhibit, c.detail))
    return out


def _apply_waivers(
    reasons: list[Reason], waivers: Sequence[Waiver], release_id: str, today: date
) -> list[Reason]:
    out = []
    for r in reasons:
        if r.severity not in (BLOCK, NO_VERDICT):
            out.append(r)
            continue
        match = [w for w in waivers if w.criterion == r.criterion]
        live = [w for w in match if w.applies(release_id, today)]
        if live:
            out.append(Reason(r.kind, WAIVED, r.criterion, r.exhibit, r.message, live[0].to_json()))
        else:
            out.append(r)
            for w in match:
                if w.release in (None, release_id):
                    out.append(
                        Reason(
                            "waiver",
                            INFO,
                            r.criterion,
                            "waivers file",
                            f"waiver by {w.owner} expired {w.expires.isoformat()}: ignored",
                        )
                    )
    return out


def _hash(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decide_release(
    candidate: Candidate,
    source: ResultSource,
    policy: ReleasePolicy,
    *,
    registry: Registry,
    waivers: Sequence[Waiver] = (),
    now: datetime | None = None,
    plan: Any | None = None,
    selection: Any | None = None,
    open_defects: Callable[[], list[Mapping[str, Any]]] | None = None,
) -> ReleaseDecision:
    """Compose the verdict for ``candidate`` from the runs ``source`` exposes."""
    today = (now or datetime.now(tz=timezone.utc)).date()
    all_runs = source.runs()
    view = EvidenceView(source)
    evidence = view.runs()
    reasons: list[Reason] = []
    for run in all_runs:
        if not run.is_evidence:
            reasons.append(
                Reason(
                    "integrity",
                    ATTENTION,
                    f"integrity:{run.run_id}",
                    f"run {run.run_id} lane {run.lane} run-trust marker",
                    f"run-trust {run.integrity or 'missing'}: ignored, not evidence",
                )
            )
    if not evidence:
        reasons.append(
            Reason(
                "integrity",
                NO_VERDICT,
                "integrity",
                "run-trust markers",
                "no run is evidence (no run with run-trust PASS)",
            )
        )
    reasons += _detector_reasons(view.verdicts(), policy)
    requirement_ids: tuple[str, ...] = ()
    if plan is not None:
        from traceability.plans import plan_requirements

        requirement_ids = tuple(
            selection.requirements if selection is not None else plan_requirements(plan, registry)
        )
    matrix = build_matrix(registry, None, view, Scope(requirements=requirement_ids))
    reasons += _requirement_reasons(matrix, policy)
    defect_reasons, defects = _defect_reasons(open_defects, policy)
    reasons += defect_reasons
    evaluation = None
    if plan is not None:
        from traceability.plans import evaluate_plan

        evaluation = evaluate_plan(
            plan, source, registry=registry, selection=selection, open_defects=open_defects
        )
        reasons += _plan_reasons(evaluation)
    reasons = _apply_waivers(reasons, waivers, candidate.release_id, today)

    severities = {r.severity for r in reasons}
    if BLOCK in severities:
        verdict = ReleaseVerdict.NO_GO
    elif NO_VERDICT in severities:
        verdict = ReleaseVerdict.NO_VERDICT
    elif WAIVED in severities:
        verdict = ReleaseVerdict.GO_WITH_WAIVERS
    else:
        verdict = ReleaseVerdict.GO

    matrix_doc = matrix.to_json()
    inputs = {
        "candidate": candidate.to_json(),
        "runs": sorted((r.run_id, r.lane, r.integrity) for r in all_runs),
        "verdicts": sorted(
            json.dumps(
                {k: v.get(k) for k in ("run_id", "nodeid", "kind", "ticket", "verdict", "subject")},
                sort_keys=True,
            )
            for v in source.verdicts()
        ),
        "matrix": [
            (r["requirement_id"], r["status"], [(c["nodeid"], c["state"]) for c in r["cases"]])
            for r in matrix_doc["rows"]
        ],
        "defects": sorted(json.dumps(d, sort_keys=True, default=str) for d in defects),
        "plan": evaluation.to_json()["criteria"] if evaluation else None,
        "plan_revision": plan.revision if plan is not None else None,
        "policy": policy.body,
        "waivers": sorted(
            json.dumps(w.to_json(), sort_keys=True)
            for w in waivers
            if w.applies(candidate.release_id, today)
        ),
    }
    order = {BLOCK: 0, NO_VERDICT: 1, WAIVED: 2, ATTENTION: 3, INFO: 4}
    reasons.sort(key=lambda r: (order[r.severity], r.kind, r.criterion))
    return ReleaseDecision(
        candidate=candidate,
        verdict=verdict,
        reasons=reasons,
        inputs_hash=_hash(inputs),
        policy_revision=policy.revision,
        evidence_runs=list(dict.fromkeys(r.run_id for r in evidence)),
        matrix=matrix_doc,
    )


def decision_json(decision: ReleaseDecision) -> dict[str, Any]:
    data = decision.to_json()
    validate(data, "release-verdict.schema.json")
    return data
