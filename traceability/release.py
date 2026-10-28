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
   verdict. With ``plan: required`` in the policy, no plan at all is no verdict.
6. **Change scope** from a promotion delta (``--delta``): a commit the promotion would
   regress, a shipped key whose detector still reproduces ("claimed fixed"), a shipped key
   no test links or mentions ("shipped untested"), and an open blocking defect that ships
   each give a reason (``change_scope`` in the policy picks block / no_verdict / attention).
   No delta is one reason, "change scope unknown", never a pass.
7. **Coverage gaps**: the gap report's ratchet; a failed ratchet breaches, a missing report
   is "coverage gaps not measured" (``coverage_gaps: block | no_verdict | ignore``).
8. **Evidence for this build**: with a pinned version (``--version`` or a candidate file's
   ``pins``), the version the run measured (``build-info.json``) must match it. Not
   measured, or a different build, is no verdict; a pinned component with no probe is
   listed as "cannot be measured".

A defect snapshot older than ``defects.max_snapshot_age_hours`` (or with no readable date)
makes the open-defects rule no verdict. Every check records its name in
``ReleaseDecision.inputs``.

**Release lines.** A detector whose defect is fixed in another release line (``fix_line``
on its known-defects manifest entry) cannot hold this release: with ``release_line`` set,
its verdict is listed under *out of scope* and neither blocks nor counts as no verdict. A
fix this release does not contain is not this release's evidence either way.

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
OUT_OF_SCOPE = "out_of_scope"
#: Every input the verdict composes; each check appends its name to ``decision.inputs``.
RELEASE_INPUTS = (
    "integrity",
    "verdicts",
    "case_links",
    "defect_snapshot",
    "plan",
    "change_scope",
    "coverage_gaps",
    "build_info",
)
CHANGE_SCOPE_CHECKS = (
    "regressing",
    "claimed_fixed",
    "shipped_untested",
    "open_defect_shipped",
    "unknown",
)
DEFAULT_SNAPSHOT_AGE_HOURS = 168
DEFAULT_COMPONENT = "app"


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
    severity: str  # block | no_verdict | attention | info | waived | out_of_scope
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
    max_snapshot_age_hours: float = DEFAULT_SNAPSHOT_AGE_HOURS
    change_scope: dict[str, str] = field(default_factory=dict)
    coverage_gaps: str = BLOCK
    plan_required: bool = False

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
        scope = {name: NO_VERDICT if name == "unknown" else BLOCK for name in CHANGE_SCOPE_CHECKS}
        scope.update(body.get("change_scope") or {})
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
            max_snapshot_age_hours=float(
                (defects or {}).get("max_snapshot_age_hours", DEFAULT_SNAPSHOT_AGE_HOURS)
            ),
            change_scope=scope,
            coverage_gaps=str(body.get("coverage_gaps") or BLOCK),
            plan_required=body.get("plan") == "required",
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
    inputs: list[str] = field(default_factory=list)
    enforcement: str = "fail"

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
            "inputs": list(self.inputs),
            "enforcement": self.enforcement,
        }

    @property
    def first_line(self) -> str:
        """Machine-readable: ``RELEASE=<verdict> ENFORCEMENT=<LEVEL> CANDIDATE=<id>``."""
        return (
            f"RELEASE={self.verdict.value} ENFORCEMENT={self.enforcement.upper()} "
            f"CANDIDATE={self.candidate.candidate_id}"
        )

    def to_markdown(self) -> str:
        c = self.candidate
        lines = [
            self.first_line,
            "",
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
            (OUT_OF_SCOPE, "Out of scope"),
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


_STATUS_HINTS = {
    "UNCOVERED": " (no case link names it)",
    "PASSING": " (no proved coverage verdict)",
    "NOT_RUN": " (no linked test ran)",
    "FLAKY": " (passed only on a rerun)",
    "FIX_OBSERVED": " (a detector cleared: verify the fix)",
}


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
                    f"{prio} requirement is {status}: not enough evidence"
                    + _STATUS_HINTS.get(status, ""),
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


def _detector_reasons(
    verdicts: Iterable[Mapping[str, Any]],
    policy: ReleasePolicy,
    release_line: str | None = None,
    fix_lines: Mapping[str, str] | None = None,
) -> list[Reason]:
    rows = list(verdicts)
    out = []
    fix_lines = fix_lines or {}
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
        line = fix_lines.get(ticket)
        if release_line and line and line != release_line:
            out.append(
                Reason(
                    "detector",
                    OUT_OF_SCOPE,
                    criterion,
                    exhibit,
                    f"{agg.verdict}: fix ships in release line {line}, not {release_line}",
                )
            )
        elif agg.verdict in policy.detector_block:
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


def _snapshot_age_reason(
    open_defects: Callable[[], Any], policy: ReleasePolicy, now: datetime
) -> tuple[Reason | None, str | None]:
    """No verdict when the snapshot is older than the policy allows or has no readable date."""
    from traceability.declared import age_line, parse_time

    stamp_of = getattr(open_defects, "generated_at", None)
    raw = stamp_of() if callable(stamp_of) else None
    stamp = parse_time(raw)
    if stamp is None:
        return (
            Reason(
                "defect",
                NO_VERDICT,
                "defects:age",
                "defect snapshot",
                f"defect snapshot has no readable generated_at ({raw!r}): its age is unknown",
            ),
            raw,
        )
    hours = (now - stamp).total_seconds() / 3600
    if hours > policy.max_snapshot_age_hours:
        return (
            Reason(
                "defect",
                NO_VERDICT,
                "defects:age",
                "defect snapshot",
                f"{age_line(raw, now)}: older than {policy.max_snapshot_age_hours:g} h, "
                f"refresh with `defects sync`",
            ),
            raw,
        )
    return None, raw


def _defect_reasons(
    open_defects: Callable[[], list[Mapping[str, Any]]] | None,
    policy: ReleasePolicy,
    now: datetime | None = None,
) -> tuple[list[Reason], list[dict[str, Any]], str | None]:
    if not policy.check_defects:
        return [], [], None
    if open_defects is None:
        return (
            [
                Reason(
                    "defect",
                    NO_VERDICT,
                    "defects",
                    "tracker",
                    "the policy checks open defects but no tracker is configured",
                )
            ],
            [],
            None,
        )
    try:
        defects = [dict(d) for d in open_defects()]
    except Exception as exc:  # an unreadable tracker is no evidence, never "zero open"
        return (
            [
                Reason(
                    "defect", NO_VERDICT, "defects", "tracker", f"defect tracker unreachable: {exc}"
                )
            ],
            [],
            None,
        )
    out = []
    age, generated_at = _snapshot_age_reason(
        open_defects, policy, now or datetime.now(tz=timezone.utc)
    )
    if age is not None:
        out.append(age)
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
    return out, defects, generated_at


def _is_blocking_defect(d: Mapping[str, Any], policy: ReleasePolicy) -> bool:
    labels = set(d.get("labels") or ())
    return d.get("severity") in policy.defect_severities or bool(labels & policy.defect_labels)


# ---------------------------------------------------------------------- change scope


def normalise_delta(doc: Mapping[str, Any]) -> dict[str, Any]:
    """A promotion delta in one shape: ``keys``, ``regressing``, ``not_compared``, ``pins``.

    Reads the promotion-diff ``--json`` output (``services[].functional[]`` with ``key``,
    ``tests``; ``services[].regressing``; top-level ``pins``) and also a flat
    ``keys``/``regressing`` document.
    """
    if "keys" in doc:
        return {
            "keys": [dict(k) for k in doc.get("keys") or ()],
            "regressing": [dict(r) for r in doc.get("regressing") or ()],
            "not_compared": list(doc.get("not_compared") or ()),
            "pins": _pins(doc.get("candidate_pins") or doc.get("pins")),
        }
    keys: dict[str, dict[str, Any]] = {}
    regressing, not_compared = [], []
    for svc in doc.get("services") or ():
        name = str(svc.get("service") or "")
        for item in svc.get("functional") or ():
            key = str(item.get("key") or "")
            if not key:
                continue
            entry = keys.setdefault(key, {"key": key, "services": [], "mentioned_by": []})
            entry["services"].append(name)
            entry["mentioned_by"] += [str(t) for t in item.get("tests") or ()]
        if svc.get("regressing"):
            regressing.append({"service": name, "commits": list(svc["regressing"])})
        if svc.get("notCompared"):
            not_compared.append(name)
    return {
        "keys": list(keys.values()),
        "regressing": regressing,
        "not_compared": not_compared,
        "pins": _pins(doc.get("pins")),
    }


def _pins(raw: Any) -> dict[str, str]:
    """``{component: version}``; a ``{component: {to|candidate: version}}`` form is accepted."""
    out: dict[str, str] = {}
    for name, value in (raw or {}).items() if isinstance(raw, Mapping) else ():
        if isinstance(value, Mapping):
            value = value.get("to") or value.get("candidate") or value.get("version")
        if value:
            out[str(name)] = str(value)
    return out


def _commit_label(commit: Any) -> str:
    if isinstance(commit, Mapping):
        return str(commit.get("sha") or commit.get("commit") or commit.get("id") or commit)[:12]
    return str(commit)[:12]


def _change_scope_reasons(
    delta: Mapping[str, Any] | None,
    policy: ReleasePolicy,
    verdicts: Iterable[Mapping[str, Any]],
    links: CaseLinks,
    defects: Sequence[Mapping[str, Any]],
) -> list[Reason]:
    rules = policy.change_scope

    def reason(check: str, criterion: str, exhibit: str, message: str) -> Reason:
        return Reason("change", rules[check], criterion, exhibit, message)

    if delta is None:
        return [
            reason(
                "unknown",
                "change-scope",
                "promotion delta",
                "change scope unknown: no promotion delta given (--delta), so what ships is "
                "not checked against the evidence",
            )
        ]
    out = []
    for item in delta["regressing"]:
        commits = ", ".join(_commit_label(c) for c in item.get("commits") or ()) or "commits"
        out.append(
            reason(
                "regressing",
                f"regressing:{item.get('service')}",
                "promotion delta",
                f"the promotion would regress {item.get('service')}: {commits}",
            )
        )
    keys = {str(k["key"]): k for k in delta["keys"]}
    still = {
        t: agg
        for t, agg in aggregate_defects(list(verdicts)).items()
        if agg.verdict == "reproduces" and t in keys
    }
    for ticket in sorted(still):
        out.append(
            reason(
                "claimed_fixed",
                f"shipped:{ticket}",
                f"verdicts for {ticket}",
                f"{ticket} ships as fixed but its detector still reproduces",
            )
        )
    linked = {r for link in links.links for r in link.requirements}
    for key, entry in sorted(keys.items()):
        if key not in linked and not entry.get("mentioned_by") and key not in still:
            out.append(
                reason(
                    "shipped_untested",
                    f"shipped:{key}",
                    "promotion delta",
                    f"{key} ships untested: no case link and no test mentions it",
                )
            )
    for d in defects:
        if str(d.get("id")) in keys and _is_blocking_defect(d, policy):
            out.append(
                reason(
                    "open_defect_shipped",
                    f"shipped:{d.get('id')}",
                    str(d.get("id")),
                    f"{d.get('id')} ships but is an open blocking defect",
                )
            )
    for service in delta["not_compared"]:
        out.append(
            Reason(
                "change",
                ATTENTION,
                f"not-compared:{service}",
                "promotion delta",
                f"{service} was not compared",
            )
        )
    return out


# ---------------------------------------------------------------------- reports


def _report_files(reports: Path | None, name: str) -> list[Path]:
    return sorted(Path(reports).rglob(name)) if reports is not None else []


def _gap_reasons(reports: Path | None, policy: ReleasePolicy) -> tuple[list[Reason], Any]:
    if policy.coverage_gaps == "ignore":
        return [], None
    severity = BLOCK if policy.coverage_gaps == BLOCK else NO_VERDICT
    ratchets = []
    for path in _report_files(reports, "gaps.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("ratchet"), dict):
            ratchets.append((str(path), data["ratchet"]))
    if not ratchets:
        return [
            Reason(
                "coverage",
                NO_VERDICT,
                "coverage-gaps",
                "gaps.json",
                "coverage gaps not measured: no gap report with a ratchet result",
            )
        ], None
    out = []
    for where, r in ratchets:
        if not r.get("passed"):
            listed = list(r.get("new") or ()) or [f"stale: {x}" for x in r.get("closed") or ()]
            out.append(
                Reason(
                    "coverage",
                    severity,
                    "coverage-gaps",
                    where,
                    "coverage-gap ratchet failed: " + ", ".join(listed[:10]),
                )
            )
    return out, [r for _, r in ratchets]


def _norm_version(raw: Any) -> str:
    text = str(raw or "").strip()
    return text[1:] if text[:1] in ("v", "V") else text


def _build_reasons(
    reports: Path | None, pins: Mapping[str, str], evidence_runs: Sequence[str]
) -> tuple[list[Reason], list[dict[str, Any]]]:
    if not pins:
        return [], []
    measured: dict[str, list[dict[str, Any]]] = {}
    for path in _report_files(reports, "build-info*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("run_id") and evidence_runs and data["run_id"] not in evidence_runs:
            continue
        measured.setdefault(str(data.get("component") or DEFAULT_COMPONENT), []).append(data)
    out = []
    for component, pinned in sorted(pins.items()):
        criterion = f"build:{component}"
        rows = measured.get(component)
        if rows is None:
            out.append(
                Reason(
                    "build",
                    NO_VERDICT,
                    criterion,
                    "build-info.json",
                    f"version not measured: {component} cannot be measured (no version probe "
                    f"wrote a build-info.json for it)",
                )
            )
            continue
        values = {_norm_version(r.get("version")) for r in rows if r.get("measured")}
        if not values:
            out.append(
                Reason(
                    "build",
                    NO_VERDICT,
                    criterion,
                    "build-info.json",
                    f"{component}: version not measured (the probe found no version)",
                )
            )
        elif values != {_norm_version(pinned)}:
            out.append(
                Reason(
                    "build",
                    NO_VERDICT,
                    criterion,
                    "build-info.json",
                    f"evidence for a different build: {component} pinned {pinned}, the run "
                    f"measured {', '.join(sorted(values))}",
                )
            )
    return out, [r for rows in measured.values() for r in rows]


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
    release_line: str | None = None,
    fix_lines: Mapping[str, str] | None = None,
    delta: Mapping[str, Any] | None = None,
    reports_dir: Path | None = None,
    pins: Mapping[str, str] | None = None,
    input_problems: Sequence[str] = (),
    enforcement: str = "fail",
) -> ReleaseDecision:
    """Compose the verdict for ``candidate`` from the runs ``source`` exposes.

    ``fix_lines`` maps a defect id to the release line its fix ships in; with
    ``release_line`` set, detectors for other lines are out of scope.

    ``reports_dir`` is where ``gaps.json`` and ``build-info.json`` are looked for (default:
    the artefact source's directory). ``pins`` maps component -> version; the candidate's
    ``version`` pins the default component.
    """
    moment = now or datetime.now(tz=timezone.utc)
    today = moment.date()
    if reports_dir is None:
        reports_dir = getattr(source, "reports_dir", None)
    all_runs = source.runs()
    view = EvidenceView(source)
    evidence = view.runs()
    reasons: list[Reason] = [
        Reason("input", ATTENTION, "input", "release decide arguments", problem)
        for problem in input_problems
    ]
    considered: list[str] = ["integrity"]
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
    considered.append("verdicts")
    reasons += _detector_reasons(view.verdicts(), policy, release_line, fix_lines)
    requirement_ids: tuple[str, ...] = ()
    if plan is not None:
        from traceability.plans import plan_requirements

        requirement_ids = tuple(
            selection.requirements if selection is not None else plan_requirements(plan, registry)
        )
    considered.append("case_links")
    matrix = build_matrix(registry, None, view, Scope(requirements=requirement_ids))
    reasons += _requirement_reasons(matrix, policy)
    considered.append("defect_snapshot")
    defect_reasons, defects, snapshot_at = _defect_reasons(open_defects, policy, moment)
    reasons += defect_reasons
    considered.append("plan")
    evaluation = None
    if plan is not None:
        from traceability.plans import evaluate_plan

        evaluation = evaluate_plan(
            plan, source, registry=registry, selection=selection, open_defects=open_defects
        )
        reasons += _plan_reasons(evaluation)
    elif policy.plan_required:
        reasons.append(
            Reason(
                "plan", NO_VERDICT, "plan", "--plan", "no test plan given; the policy requires one"
            )
        )
    considered.append("change_scope")
    scope = normalise_delta(delta) if delta is not None else None
    reasons += _change_scope_reasons(scope, policy, view.verdicts(), view.links(), defects)
    considered.append("coverage_gaps")
    gap_reasons, gap_ratchets = _gap_reasons(reports_dir, policy)
    reasons += gap_reasons
    considered.append("build_info")
    pinned = dict((scope or {}).get("pins") or {})
    pinned.update(pins or {})
    if candidate.version:
        pinned[DEFAULT_COMPONENT] = candidate.version
    evidence_ids = [r.run_id for r in evidence]
    build_reasons, measured = _build_reasons(reports_dir, pinned, evidence_ids)
    reasons += build_reasons
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
        "defect_snapshot_generated_at": snapshot_at,
        "delta": scope,
        "coverage_gaps": gap_ratchets,
        "pins": pinned,
        "build_info": sorted(json.dumps(m, sort_keys=True, default=str) for m in measured),
        "plan": evaluation.to_json()["criteria"] if evaluation else None,
        "plan_revision": plan.revision if plan is not None else None,
        "policy": policy.body,
        "waivers": sorted(
            json.dumps(w.to_json(), sort_keys=True)
            for w in waivers
            if w.applies(candidate.release_id, today)
        ),
    }
    if release_line:  # absent otherwise, so earlier verdicts keep their hash
        inputs["release_line"] = release_line
        inputs["fix_lines"] = sorted((fix_lines or {}).items())
    order = {BLOCK: 0, NO_VERDICT: 1, WAIVED: 2, ATTENTION: 3, OUT_OF_SCOPE: 4, INFO: 5}
    reasons.sort(key=lambda r: (order[r.severity], r.kind, r.criterion))
    return ReleaseDecision(
        candidate=candidate,
        verdict=verdict,
        reasons=reasons,
        inputs_hash=_hash(inputs),
        policy_revision=policy.revision,
        evidence_runs=list(dict.fromkeys(r.run_id for r in evidence)),
        matrix=matrix_doc,
        inputs=considered,
        enforcement=enforcement,
    )


def decision_json(decision: ReleaseDecision) -> dict[str, Any]:
    data = decision.to_json()
    validate(data, "release-verdict.schema.json")
    return data
