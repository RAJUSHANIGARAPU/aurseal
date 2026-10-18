"""PlatformService: every read and write the platform offers, framework-free.

The REST API, the UI and the CLI are adapters over this class, so they cannot diverge:
access checks, request validation and the run-trust judgement live here once. Every
public method takes the calling :class:`~qaplatform.auth.tokens.Principal` and, where it
touches project data, the project key; it resolves a project-bound
:class:`~qaplatform.store.ScopedStore` through :meth:`_scope` before anything else.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from gate.integrity import Thresholds
from qaplatform import audit, exploratory, manual, schemas
from qaplatform.auth import tokens
from qaplatform.auth.tokens import Principal
from qaplatform.dispatch import NullDispatcher, RunDispatcher
from qaplatform.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    NotConfigured,
    NotFound,
    RunClosed,
)
from qaplatform.ingest.junit import parse_junit
from qaplatform.ingest.verdicts import accept_row, parse_verdict_lines
from qaplatform.rbac import Permission, Role, authorize
from qaplatform.readers import ReleaseVerdictReader, StoreTraceability, TraceabilityReader
from qaplatform.store import PlatformStore, ScopedStore
from qaplatform.summary import judge, run_summary
from results._util import to_utc, utc_now
from results.catalogue import case_id_for_nodeid, case_key_from_nodeid
from results.models import (
    OUTCOMES,
    CaseRecord,
    EvidenceRecord,
    ResultRecord,
    RevisionRecord,
    RunRecord,
    VerdictRecord,
)

# The permission each method requires (qaplatform/rbac.py holds the matrix).
READ = Permission.READ
PUSH_RESULTS = Permission.PUSH_RESULTS
RECORD_MANUAL = Permission.RECORD_MANUAL
TRIGGER_RUN = Permission.TRIGGER_RUN
READ_AUDIT = Permission.READ_AUDIT
MANAGE_TOKENS = Permission.MANAGE_TOKENS
MANAGE_MEMBERS = Permission.MANAGE_MEMBERS
MANAGE_PROJECT = Permission.MANAGE_PROJECT

MAX_PAGE = 1000
IDEMPOTENCY_TTL = timedelta(hours=24)
_FRAMEWORK_DEFAULT = "external"


def _encode_cursor(value: int) -> str:
    return base64.urlsafe_b64encode(str(value).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = int(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadRequest("cursor is not one this server issued") from exc
    if value < 0:
        raise BadRequest("cursor is not one this server issued")
    return value


def _limit(limit: int | str | None, default: int = 100) -> int:
    if limit in (None, ""):
        return default
    try:
        value = int(limit)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise BadRequest("limit must be an integer") from exc
    if not 1 <= value <= MAX_PAGE:
        raise BadRequest(f"limit must be between 1 and {MAX_PAGE}")
    return value


def _validated(name: str, payload: Any) -> None:
    errors = schemas.errors_for(name, payload)
    if errors:
        raise BadRequest(f"{len(errors)} schema violation(s) against {name}", errors=errors)


def _steps_text(steps: Sequence[dict[str, Any]]) -> str:
    lines = []
    for s in sorted(steps, key=lambda s: s["no"]):
        line = f"step {s['no']} [{s['status']}]"
        if s.get("title"):
            line += f" {s['title']}"
        if s.get("actual"):
            line += f" - actual: {s['actual']}"
        lines.append(line)
    return "\n".join(lines)


_IMAGE = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _evidence_kind(uri: str) -> str:
    return "screenshot" if uri.lower().endswith(_IMAGE) else "other"


def _fingerprint(test_id: str, item: dict[str, Any]) -> str | None:
    """Groups the same failure across runs: test id plus the message's first line."""
    if item["outcome"] not in ("failed", "error"):
        return None
    first = (item.get("message") or "").strip().splitlines()[:1]
    basis = f"{test_id}|{first[0] if first else ''}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


class PlatformService:
    def __init__(
        self,
        store: PlatformStore,
        *,
        dispatcher: RunDispatcher | None = None,
        traceability: TraceabilityReader | None = None,
        release_verdicts: ReleaseVerdictReader | None = None,
        thresholds: Thresholds | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.dispatcher = dispatcher or NullDispatcher()
        self.traceability_reader = traceability or StoreTraceability()
        self.release_verdict_reader = release_verdicts
        self.thresholds = thresholds
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ access

    def authenticate(self, raw_token: str | None) -> Principal:
        """The principal behind a bearer token (401 when missing/unknown/expired/revoked)."""
        return tokens.verify(self.store, raw_token, now=self.clock())

    def _scope(self, principal: Principal, project: str, permission: str) -> ScopedStore:
        """The one gate every project method passes: visibility first, then the role.

        A project the caller cannot see is reported as not found (existence is not
        disclosed); a visible project the role may not act on is forbidden.
        """
        if principal.project_id is not None and principal.project_id != project:
            raise NotFound(f"no project {project!r}")
        if self.store.project(project) is None:
            raise NotFound(f"no project {project!r}")
        authorize(principal.role, permission)
        return self.store.scoped(project)

    @contextmanager
    def _write(
        self,
        principal: Principal,
        project: str,
        permission: str,
        action: str,
        target: tuple[str, str],
        *,
        schema: tuple[str, Any] | None = None,
        allow_archived: bool = False,
    ) -> Iterator[tuple[ScopedStore, dict[str, Any]]]:
        """One write transaction that also appends the audit row describing it.

        The body fills ``diff`` with ids and counts. The audit row is written in the same
        transaction after the body succeeds; if either fails, neither is kept. A body that
        leaves ``diff`` empty changed nothing (an idempotent replay) and is not audited.
        """
        scoped = self._scope(principal, project, permission)
        if not allow_archived and scoped.project().get("archived_at"):
            raise Conflict(f"project {project!r} is archived: it is read-only")
        if schema is not None:  # after the access check: a caller without the
            _validated(*schema)  # permission learns nothing about the payload rules
        diff: dict[str, Any] = {}
        with self.store.transaction():
            yield scoped, diff
            if diff:
                target_id = target[1] or str(diff.get("run_id") or diff.get("id") or "")
                audit.record(
                    scoped,
                    actor_user_id=principal.user_id,
                    token_id=principal.token_id,
                    action=action,
                    target=(target[0], target_id),
                    diff=diff,
                )

    def me(self, principal: Principal) -> dict[str, Any]:
        from qaplatform.rbac import MATRIX

        doc = principal.to_json()
        doc["permissions"] = sorted(p.value for p in MATRIX[Role(principal.role)])
        return doc

    def health(self) -> dict[str, Any]:
        return {"status": "ok"}

    def ready(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.store.ready() else "unavailable",
            "schema_version": schemas.SCHEMA_VERSION,
        }

    # ------------------------------------------------------------------ helpers

    def _run(self, scoped: ScopedStore, run_id: str) -> dict[str, Any]:
        run = scoped.run(run_id)
        if run is None:
            raise NotFound(f"no run {run_id!r} in project {scoped.project_id!r}")
        return run

    def _open_run(self, scoped: ScopedStore, run_id: str) -> dict[str, Any]:
        run = self._run(scoped, run_id)
        if run["status"] != "running":
            raise RunClosed(f"run {run_id} was finished at {run.get('finished_at')}")
        return run

    def _summary(self, scoped: ScopedStore, run: dict[str, Any]) -> dict[str, Any]:
        finals = scoped.final_results(run["run_id"])
        verdicts = scoped.run_verdicts(run["run_id"])
        if run["status"] == "running":
            integrity = judge(finals, verdicts, run.get("expected_count"), self.thresholds)
            return run_summary(scoped.project_id, run, finals, verdicts, integrity=integrity)
        return run_summary(scoped.project_id, run, finals, verdicts)

    # ------------------------------------------------------------------ push

    def create_run(
        self, principal: Principal, project: str, body: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """``(created, run summary)``; an ``external_id`` already known returns that run."""
        with self._write(
            principal, project, PUSH_RESULTS, "run.create", ("run", ""), schema=("run-create", body)
        ) as (
            scoped,
            diff,
        ):
            external = body.get("external_id")
            if external:
                existing = scoped.run_by_external_id(external)
                if existing is not None:
                    return False, self._summary(scoped, existing)
            request_id = body.get("run_request_id")
            if request_id and scoped.run_request(request_id) is None:
                raise BadRequest(f"no run request {request_id!r} in project {project!r}")
            prov = dict(body.get("provenance") or {})
            run_id = "r-" + uuid.uuid4().hex[:20]
            labels = dict(body.get("labels") or {})
            run = RunRecord(
                run_id=run_id,
                source=body.get("source") or "automated",
                external_id=external,
                name=body["name"],
                framework=prov.get("framework"),
                lane=body.get("lane"),
                environment=body.get("environment"),
                sut_version=prov.get("sut_version"),
                git_sha=prov.get("commit"),
                git_branch=prov.get("branch"),
                ci_url=prov.get("ci_url"),
                trigger="run-request" if request_id else "api",
                run_request_id=request_id,
                release_label=body.get("release"),
                expected_count=body.get("expected_count"),
                started_at=to_utc(body.get("started_at")) or utc_now(),
                status="running",
                provenance=prov or None,
                metadata={"labels": labels, "created_by": principal.user_id},
            )
            scoped.create_run(run)
            if request_id:
                scoped.update_run_request(request_id, status="running", run_id=run_id)
            diff.update(run_id=run_id, external_id=external, source=run.source)
            created = scoped.run(run_id)
            assert created is not None
            return True, self._summary(scoped, created)

    def _store_results(
        self, scoped: ScopedStore, run: dict[str, Any], items: Sequence[dict[str, Any]]
    ) -> int:
        framework = run.get("framework") or _FRAMEWORK_DEFAULT
        cases: dict[str, CaseRecord] = {}
        results: list[ResultRecord] = []
        evidence: list[EvidenceRecord] = []
        for item in items:
            test_id = item["test_id"]
            key = case_key_from_nodeid(test_id)
            case_id = case_id_for_nodeid(test_id)
            links = [("requirement", r) for r in item.get("requirements") or ()]
            links += [("defect", t) for t in item.get("tickets") or ()]
            if case_id in cases:
                merged = tuple(dict.fromkeys([*cases[case_id].links, *links]))
                cases[case_id] = replace(cases[case_id], links=merged)
            else:
                cases[case_id] = CaseRecord(
                    case_id=case_id,
                    case_key=key,
                    framework=framework,
                    title=item.get("title") or test_id,
                    module=item.get("suite"),
                    links=tuple(dict.fromkeys(links)),
                )
            attempt = int(item.get("attempt") or 1)
            trace = item.get("trace")
            if not trace and item.get("steps"):
                trace = _steps_text(item["steps"])
            results.append(
                ResultRecord(
                    run_id=run["run_id"],
                    nodeid=test_id,
                    outcome=item["outcome"],
                    attempt=attempt,
                    case_id=case_id,
                    duration_ms=item.get("duration_ms"),
                    started_at=to_utc(item.get("started_at")),
                    message=item.get("message"),
                    longrepr=trace,
                    skip_reason=item.get("skip_reason"),
                    defect_ref=(item.get("tickets") or [None])[0],
                    markers=tuple(item.get("tags") or ()),
                    failure_fingerprint=_fingerprint(test_id, item),
                )
            )
            for link in item.get("evidence") or ():
                evidence.append(self._evidence_record(run["run_id"], link, test_id, attempt))
            for step in item.get("steps") or ():
                if step.get("evidence_uri"):
                    evidence.append(
                        self._evidence_record(
                            run["run_id"],
                            {"kind": "other", "uri": step["evidence_uri"]},
                            test_id,
                            attempt,
                        )
                    )
        scoped.upsert_cases(cases.values())
        added = scoped.add_results(results)
        if evidence:
            scoped.add_evidence(evidence)
        return added

    @staticmethod
    def _evidence_record(
        run_id: str, link: dict[str, Any], test_id: str | None, attempt: int | None
    ) -> EvidenceRecord:
        return EvidenceRecord(
            run_id=run_id,
            kind=link["kind"],
            uri=link["uri"],
            nodeid=test_id,
            attempt=attempt,
            sha256=link.get("sha256"),
            bytes=link.get("size_bytes"),
        )

    def append_results(
        self, principal: Principal, project: str, run_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Append a batch (≤ 1000); a ``(test_id, attempt)`` already stored is a no-op."""
        with self._write(
            principal,
            project,
            PUSH_RESULTS,
            "results.append",
            ("run", run_id),
            schema=("results-batch", body),
        ) as (
            scoped,
            diff,
        ):
            run = self._open_run(scoped, run_id)
            items = body["results"]
            added = self._store_results(scoped, run, items)
            diff.update(received=len(items), added=added)
            return {
                "run_id": run_id,
                "received": len(items),
                "added": added,
                "duplicates": len(items) - added,
            }

    def append_junit(
        self, principal: Principal, project: str, run_id: str, xml: bytes
    ) -> dict[str, Any]:
        """Append the test cases of one JUnit XML document."""
        with self._write(principal, project, PUSH_RESULTS, "results.junit", ("run", run_id)) as (
            scoped,
            diff,
        ):
            items, rejected = parse_junit(xml)
            run = self._open_run(scoped, run_id)
            added = 0
            for start in range(0, len(items), MAX_PAGE):
                added += self._store_results(scoped, run, items[start : start + MAX_PAGE])
            diff.update(received=len(items), added=added, rejected=len(rejected))
            return {
                "run_id": run_id,
                "received": len(items),
                "added": added,
                "duplicates": len(items) - added,
                "rejected": rejected,
            }

    def append_verdicts(
        self, principal: Principal, project: str, run_id: str, body: Any
    ) -> dict[str, Any]:
        """Verdict rows as a JSON array (strict) or JSONL text (bad lines counted, dropped)."""
        jsonl = isinstance(body, (bytes, str))
        with self._write(
            principal,
            project,
            PUSH_RESULTS,
            "verdict.append",
            ("run", run_id),
            schema=None if jsonl else ("verdicts-batch", body),
        ) as (scoped, diff):
            if jsonl:
                text = body.decode("utf-8") if isinstance(body, bytes) else body
                rows, dropped = parse_verdict_lines(text.splitlines())
            else:
                rows, dropped = [accept_row(r) for r in body], 0
            self._open_run(scoped, run_id)
            records = [
                VerdictRecord(
                    run_id=run_id,
                    nodeid=r["test_id"],
                    kind=r["kind"],
                    verdict=r["verdict"],
                    ticket=r.get("ticket") or None,
                    detail=r.get("detail") or "",
                    subject=r.get("subject") or None,
                    recorded_at=r.get("timestamp") or r.get("recorded_at"),
                )
                for r in rows
                if r is not None
            ]
            added = scoped.add_verdicts(records)
            diff.update(received=len(records), added=added, dropped=dropped)
            return {"run_id": run_id, "received": len(records), "added": added, "dropped": dropped}

    def add_evidence(
        self, principal: Principal, project: str, run_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Evidence links (allowed after finish: reports are often built after the run)."""
        with self._write(
            principal,
            project,
            PUSH_RESULTS,
            "evidence.attach",
            ("run", run_id),
            schema=("evidence-batch", body),
        ) as (
            scoped,
            diff,
        ):
            self._run(scoped, run_id)
            links = body["links"]
            added = scoped.add_evidence(
                self._evidence_record(run_id, link, link.get("test_id"), link.get("attempt"))
                for link in links
            )
            diff.update(received=len(links), added=added)
            return {"run_id": run_id, "received": len(links), "added": added}

    def finish_run(
        self, principal: Principal, project: str, run_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Close the run and judge it. Finishing a finished run returns it unchanged."""
        with self._write(
            principal,
            project,
            PUSH_RESULTS,
            "run.finish",
            ("run", run_id),
            schema=("run-finish", body),
        ) as (
            scoped,
            diff,
        ):
            run = self._run(scoped, run_id)
            if run["status"] != "running":
                return self._summary(scoped, run)
            if body.get("expected_count") is not None:
                scoped.set_expected_count(run_id, int(body["expected_count"]))
                run = self._run(scoped, run_id)
            finals = scoped.final_results(run_id)
            verdicts = scoped.run_verdicts(run_id)
            integrity = judge(finals, verdicts, run.get("expected_count"), self.thresholds)
            status = "interrupted" if body.get("interrupted") else "finished"
            scoped.finish_run(run_id, status, integrity["verdict"], to_utc(body.get("finished_at")))
            if run.get("run_request_id"):
                scoped.update_run_request(
                    run["run_request_id"],
                    status="failed" if status == "interrupted" else "finished",
                )
            diff.update(status=status, integrity=integrity["verdict"])
            finished = self._run(scoped, run_id)
            return run_summary(project, finished, finals, verdicts, integrity=integrity)

    # ------------------------------------------------------------------ read

    def get_run(self, principal: Principal, project: str, run_id: str) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        return self._summary(scoped, self._run(scoped, run_id))

    def list_runs(
        self,
        principal: Principal,
        project: str,
        *,
        status: str | None = None,
        branch: str | None = None,
        source: str | None = None,
        since: str | None = None,
        cursor: str | None = None,
        limit: int | str | None = None,
    ) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        size = _limit(limit)
        offset = _decode_cursor(cursor)
        store_status = {"open": "running"}.get(status or "", status)
        rows = scoped.runs(
            status=store_status,
            branch=branch,
            source=source,
            since=since,
            limit=size + 1,
            offset=offset,
        )
        page = [
            {
                "run_id": r["run_id"],
                "external_id": r.get("external_id"),
                "name": r.get("name"),
                "status": "open" if r["status"] == "running" else r["status"],
                "source": r.get("source"),
                "release": r.get("release_label"),
                "branch": r.get("git_branch"),
                "integrity_verdict": r.get("integrity_verdict"),
                "result_count": r.get("result_count"),
                "started_at": r.get("started_at"),
                "finished_at": r.get("finished_at"),
            }
            for r in rows[:size]
        ]
        out: dict[str, Any] = {"runs": page}
        if len(rows) > size:
            out["next_cursor"] = _encode_cursor(offset + size)
        return out

    def run_results(
        self,
        principal: Principal,
        project: str,
        run_id: str,
        *,
        outcome: str | None = None,
        attempts: str | None = None,
        cursor: str | None = None,
        limit: int | str | None = None,
    ) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        self._run(scoped, run_id)
        if outcome is not None and outcome not in OUTCOMES:
            raise BadRequest(f"outcome must be one of {', '.join(OUTCOMES)}")
        size = _limit(limit)
        offset = _decode_cursor(cursor)
        read = scoped.all_results if attempts == "all" else scoped.final_results
        rows = read(run_id, outcome=outcome, limit=size + 1, offset=offset)
        keys = (
            "nodeid",
            "outcome",
            "attempt",
            "duration_ms",
            "started_at",
            "message",
            "skip_reason",
            "defect_ref",
            "attempts",
            "failed_attempts",
        )
        page = [
            {("test_id" if k == "nodeid" else k): r.get(k) for k in keys if k in r}
            for r in rows[:size]
        ]
        out: dict[str, Any] = {"run_id": run_id, "results": page}
        if len(rows) > size:
            out["next_cursor"] = _encode_cursor(offset + size)
        return out

    def run_evidence(self, principal: Principal, project: str, run_id: str) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        self._run(scoped, run_id)
        keys = ("nodeid", "attempt", "kind", "uri", "sha256", "bytes")
        return {
            "run_id": run_id,
            "links": [{k: r.get(k) for k in keys} for r in scoped.run_evidence(run_id)],
        }

    def list_verdicts(
        self,
        principal: Principal,
        project: str,
        *,
        run_id: str | None = None,
        ticket: str | None = None,
        kind: str | None = None,
        cursor: str | None = None,
        limit: int | str | None = None,
    ) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        size = _limit(limit)
        rows = scoped.verdicts_page(
            run_id=run_id, ticket=ticket, kind=kind, after=_decode_cursor(cursor), limit=size + 1
        )
        page = [
            {
                "run_id": r["run_id"],
                "test_id": r["nodeid"],
                "kind": r["kind"],
                "ticket": r["ticket"] or None,
                "subject": r["subject"] or None,
                "verdict": r["verdict"],
                "detail": r["detail"],
                "recorded_at": r["recorded_at"] or None,
            }
            for r in rows[:size]
        ]
        out: dict[str, Any] = {"verdicts": page}
        if len(rows) > size:
            out["next_cursor"] = _encode_cursor(rows[size - 1]["id"])
        return out

    def case_history(
        self, principal: Principal, project: str, test_id: str, *, limit: int | str | None = None
    ) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        rows = scoped.case_history(case_id_for_nodeid(test_id), limit=_limit(limit, 100))
        return {"test_id": test_id, "history": rows}

    def traceability(
        self,
        principal: Principal,
        project: str,
        *,
        requirement: str | None = None,
        release: str | None = None,
    ) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        return self.traceability_reader.traceability(
            scoped, requirement=requirement, release=release
        )

    def requirement(self, principal: Principal, project: str, requirement: str) -> dict[str, Any]:
        report = self.traceability(principal, project, requirement=requirement)
        return report["rows"][0]

    def release_verdict(self, principal: Principal, project: str, release: str) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        if self.release_verdict_reader is None:
            raise NotConfigured(
                "no release policy is configured; set QAP_RELEASE_POLICY (or serve from a"
                " directory with release-policy.yaml) so this server can decide a release"
            )
        return self.release_verdict_reader.release_verdict(scoped, release)

    # ------------------------------------------------------------------ trigger

    def request_run(
        self, principal: Principal, project: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Store the request, hand it to the dispatcher, return its state (HTTP 202)."""
        with self._write(
            principal,
            project,
            TRIGGER_RUN,
            "run_request.create",
            ("run_request", ""),
            schema=("run-request", body),
        ) as (scoped, diff):
            now = utc_now()
            row = {
                "id": "rq-" + uuid.uuid4().hex[:16],
                "requested_by": principal.user_id,
                "dispatcher": self.dispatcher.name,
                "target": body["target"],
                "environment": body.get("environment"),
                "release": body.get("release"),
                "params": dict(body.get("params") or {}),
                "status": "queued",
                "detail": None,
                "external_ref": None,
                "run_id": None,
                "created_at": now,
                "updated_at": now,
            }
            scoped.insert_run_request(row)
            result = self.dispatcher.dispatch(row)
            scoped.update_run_request(
                row["id"],
                status=result.status,
                detail=result.detail,
                external_ref=result.external_ref,
            )
            diff.update(id=row["id"], target=row["target"], status=result.status)
            return self._run_request_doc(scoped, row["id"])

    def get_run_request(
        self, principal: Principal, project: str, request_id: str
    ) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        return self._run_request_doc(scoped, request_id)

    def _run_request_doc(self, scoped: ScopedStore, request_id: str) -> dict[str, Any]:
        row = scoped.run_request(request_id)
        if row is None:
            raise NotFound(f"no run request {request_id!r}")
        return {
            "schema_version": schemas.SCHEMA_VERSION,
            "id": row["id"],
            "project": scoped.project_id,
            "target": row["target"],
            "environment": row.get("environment"),
            "release": row.get("release"),
            "params": row.get("params") or {},
            "status": row["status"],
            "dispatcher": row["dispatcher"],
            "detail": row.get("detail"),
            "run_id": row.get("run_id"),
            "external_ref": row.get("external_ref"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ------------------------------------------------------------------ projects

    @staticmethod
    def _project_doc(row: dict[str, Any], role: str | None) -> dict[str, Any]:
        return {
            "key": row["id"],
            "name": row.get("name") or row["id"],
            "archived": bool(row.get("archived_at")),
            "archived_at": row.get("archived_at"),
            "created_at": row.get("created_at"),
            "settings": json.loads(row.get("settings_json") or "{}"),
            "role": role,
        }

    def _instance_admin(self, principal: Principal) -> bool:
        if principal.project_id is not None:
            return False
        user = self.store.user(principal.user_id)
        return bool(user and user["is_instance_admin"]) or principal.token_id is None

    def list_projects(self, principal: Principal) -> dict[str, Any]:
        """Only projects the caller can see: every project for an instance admin, else the
        token's own project (a project token never reveals the others)."""
        if self._instance_admin(principal):
            rows = [(r, "admin") for r in self.store.projects()]
        else:
            rows = [
                (r, r["role"])
                for r in self.store.projects_of(principal.user_id)
                if r["id"] == principal.project_id
            ]
        return {"projects": [self._project_doc(r, role) for r, role in rows]}

    def get_project(self, principal: Principal, project: str) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        return self._project_doc(scoped.project(), principal.role)

    def create_project(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        """Instance admins only. The creator becomes the project's first admin."""
        if not self._instance_admin(principal):
            raise Forbidden("only an instance admin can create projects")
        _validated("project-create", body)
        key = body["key"]
        if self.store.project(key) is not None:
            raise Conflict(f"project {key!r} already exists")
        with self.store.transaction():
            self.store.create_project(key, body.get("name", ""), body.get("settings"))
            self.store.ensure_user(principal.user_id)
            scoped = self.store.scoped(key)
            scoped.grant(principal.user_id, Role.ADMIN.value, principal.user_id)
            audit.record(
                scoped,
                actor_user_id=principal.user_id,
                token_id=principal.token_id,
                action="project.create",
                target=("project", key),
                diff={"key": key, "name": body.get("name", "")},
            )
        return self._project_doc(scoped.project(), Role.ADMIN.value)

    def update_project(
        self, principal: Principal, project: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Rename, change settings, archive (read-only from then on) or unarchive."""
        with self._write(
            principal,
            project,
            MANAGE_PROJECT,
            "project.update",
            ("project", project),
            schema=("project-update", body),
            allow_archived=True,
        ) as (scoped, diff):
            fields: dict[str, Any] = {}
            if "name" in body:
                fields["name"] = body["name"]
            if "settings" in body:
                fields["settings_json"] = json.dumps(body["settings"], sort_keys=True)
            if "archived" in body:
                was = bool(scoped.project().get("archived_at"))
                if body["archived"] != was:
                    fields["archived_at"] = utc_now() if body["archived"] else None
            scoped.update_project(**fields)
            if fields:
                diff.update({k: v for k, v in body.items() if k != "schema_version"})
            return self._project_doc(scoped.project(), principal.role)

    # ------------------------------------------------------------------ manual execution

    def start_manual_run(
        self, principal: Principal, project: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """A run of source ``manual`` with one execution per case, each pinned to the case
        revision it follows. ``expected_count`` is the number of cases, so an unfinished
        execution keeps the run from reading as complete."""
        with self._write(
            principal,
            project,
            RECORD_MANUAL,
            "manual_run.start",
            ("run", ""),
            schema=("manual-run-create", body),
        ) as (scoped, diff):
            if body.get("cases"):
                cases = [
                    manual.ManualCase(
                        key=c["key"],
                        title=c.get("title") or c["key"],
                        steps=list(c["steps"]),
                        expected=list(c.get("expected") or []),
                        preconditions=list(c.get("preconditions") or []),
                        requirements=list(c.get("requirements") or []),
                    )
                    for c in body["cases"]
                ]
            else:
                cases = manual.parse_spec(body["spec"])
            if not cases:
                raise BadRequest(
                    "no case with steps found (a spec needs '## Scenario' + '### Steps')"
                )
            keys = [c.key for c in cases]
            if len(keys) != len(set(keys)):
                raise BadRequest("case keys must be unique within a manual run")
            if body.get("external_id") and scoped.run_by_external_id(body["external_id"]):
                raise Conflict(f"a run with external_id {body['external_id']!r} exists")
            run_id = "r-" + uuid.uuid4().hex[:20]
            now = utc_now()
            scoped.create_run(
                RunRecord(
                    run_id=run_id,
                    source="manual",
                    external_id=body.get("external_id"),
                    name=body["name"],
                    framework="manual",
                    environment=body.get("environment"),
                    trigger="manual",
                    release_label=body.get("release"),
                    expected_count=len(cases),
                    started_at=now,
                    metadata={"created_by": principal.user_id},
                )
            )
            for case in cases:
                doc = case.to_json()
                case_id = case_id_for_nodeid(case.key)
                rev = manual.revision_id(doc)
                scoped.upsert_cases(
                    [
                        CaseRecord(
                            case_id=case_id,
                            case_key=case_key_from_nodeid(case.key),
                            framework="manual",
                            title=case.title,
                            description="\n".join(case.preconditions) or None,
                            steps=tuple(case.steps),
                            links=tuple(("requirement", r) for r in case.requirements),
                        )
                    ]
                )
                scoped.add_revisions(
                    [
                        RevisionRecord(
                            revision_id=rev,
                            case_id=case_id,
                            steps=tuple(case.steps),
                            first_seen_run_id=run_id,
                        )
                    ]
                )
                scoped.insert_manual_execution(
                    {
                        "id": "mx-" + uuid.uuid4().hex[:16],
                        "run_id": run_id,
                        "case_id": case_id,
                        "case_key": case.key,
                        "revision_id": rev,
                        "title": case.title,
                        "steps_json": json.dumps(case.steps, ensure_ascii=False),
                        "expected_json": json.dumps(case.expected, ensure_ascii=False),
                        "requirements_json": json.dumps(case.requirements),
                        "tester_user_id": principal.user_id,
                        "started_at": now,
                    }
                )
            diff.update(run_id=run_id, cases=len(cases))
            return self._manual_run_doc(scoped, run_id)

    def get_manual_run(self, principal: Principal, project: str, run_id: str) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        return self._manual_run_doc(scoped, run_id)

    def _manual_run_doc(self, scoped: ScopedStore, run_id: str) -> dict[str, Any]:
        run = self._run(scoped, run_id)
        if run.get("source") != "manual":
            raise NotFound(f"run {run_id!r} is not a manual run")
        executions = []
        for ex in scoped.manual_executions(run_id):
            ex["step_results"] = scoped.manual_steps(ex["id"])
            ex["missing_steps"] = manual.missing_steps(len(ex["steps"]), ex["step_results"])
            executions.append(ex)
        return {"run": self._summary(scoped, run), "executions": executions}

    def _execution(self, scoped: ScopedStore, execution_id: str) -> dict[str, Any]:
        ex = scoped.manual_execution(execution_id)
        if ex is None:
            raise NotFound(f"no manual execution {execution_id!r}")
        return ex

    def record_manual_step(
        self,
        principal: Principal,
        project: str,
        execution_id: str,
        step_no: int | str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Record (or correct, until the execution is finished) one step's result."""
        with self._write(
            principal,
            project,
            RECORD_MANUAL,
            "manual.step",
            ("manual_execution", execution_id),
            schema=("manual-step", body),
        ) as (scoped, diff):
            ex = self._execution(scoped, execution_id)
            self._open_run(scoped, ex["run_id"])
            if ex["status"] != "in_progress":
                raise Conflict(f"execution {execution_id} is already {ex['status']}")
            try:
                number = int(step_no)
            except (TypeError, ValueError) as exc:
                raise BadRequest("step number must be an integer") from exc
            if not 1 <= number <= len(ex["steps"]):
                raise BadRequest(f"step must be between 1 and {len(ex['steps'])}")
            scoped.record_manual_step(
                execution_id,
                {
                    "step_no": number,
                    "status": body["status"],
                    "actual": body.get("actual", ""),
                    "evidence_uri": body.get("evidence_uri") or None,
                    "recorded_by": principal.user_id,
                    "recorded_at": utc_now(),
                },
            )
            diff.update(step=number, status=body["status"])
            steps = scoped.manual_steps(execution_id)
            return {
                "execution_id": execution_id,
                "step_no": number,
                "status": body["status"],
                "missing_steps": manual.missing_steps(len(ex["steps"]), steps),
            }

    def finish_manual_execution(
        self, principal: Principal, project: str, execution_id: str
    ) -> dict[str, Any]:
        """Turn the recorded steps into one result (and coverage verdicts for a pass)."""
        with self._write(
            principal,
            project,
            RECORD_MANUAL,
            "manual.finish",
            ("manual_execution", execution_id),
        ) as (scoped, diff):
            ex = self._execution(scoped, execution_id)
            run = self._open_run(scoped, ex["run_id"])
            if ex["status"] != "in_progress":
                raise Conflict(f"execution {execution_id} is already {ex['status']}")
            steps = scoped.manual_steps(execution_id)
            missing = manual.missing_steps(len(ex["steps"]), steps)
            if missing:
                raise BadRequest(f"steps not recorded yet: {', '.join(map(str, missing))}")
            result = manual.outcome_of(steps)
            scoped.add_results(
                [
                    ResultRecord(
                        run_id=run["run_id"],
                        nodeid=ex["case_key"],
                        outcome=result.outcome,
                        case_id=ex["case_id"],
                        revision_id=ex["revision_id"],
                        started_at=ex["started_at"],
                        finished_at=utc_now(),
                        message=result.message or None,
                        longrepr=manual.steps_text(ex["steps"], steps),
                        skip_reason=result.skip_reason,
                        worker=principal.user_id,
                    )
                ]
            )
            evidence = [
                EvidenceRecord(
                    run_id=run["run_id"],
                    kind=_evidence_kind(s["evidence_uri"]),
                    uri=s["evidence_uri"],
                    nodeid=ex["case_key"],
                    attempt=1,
                )
                for s in steps
                if s.get("evidence_uri")
            ]
            if evidence:
                scoped.add_evidence(evidence)
            if result.coverage:
                scoped.add_verdicts(
                    VerdictRecord(
                        run_id=run["run_id"],
                        nodeid=ex["case_key"],
                        kind="coverage",
                        verdict=result.coverage,
                        subject=req,
                        detail="manual execution; "
                        + (
                            "every passing step has evidence"
                            if result.coverage == "proved"
                            else "no evidence attached to every passing step"
                        ),
                        recorded_at=utc_now(),
                    )
                    for req in ex["requirements"]
                )
            scoped.finish_manual_execution(execution_id, result.status, utc_now())
            diff.update(status=result.status, outcome=result.outcome, coverage=result.coverage)
            return {
                "execution_id": execution_id,
                "case_key": ex["case_key"],
                "status": result.status,
                "outcome": result.outcome,
                "message": result.message,
                "coverage": result.coverage,
            }

    # ------------------------------------------------------------------ exploratory sessions

    def start_session(
        self, principal: Principal, project: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Open an exploratory session (and its run, source ``exploratory``)."""
        with self._write(
            principal,
            project,
            RECORD_MANUAL,
            "session.start",
            ("session", ""),
            schema=("session-start", body),
        ) as (scoped, diff):
            session_id = "xs-" + uuid.uuid4().hex[:16]
            run_id = "r-" + uuid.uuid4().hex[:20]
            now = utc_now()
            scoped.create_run(
                RunRecord(
                    run_id=run_id,
                    source="exploratory",
                    name=body["charter"][:200],
                    framework="exploratory",
                    trigger="session",
                    release_label=body.get("release"),
                    started_at=now,
                    metadata={"created_by": principal.user_id, "session_id": session_id},
                )
            )
            scoped.insert_session(
                {
                    "id": session_id,
                    "run_id": run_id,
                    "charter": body["charter"],
                    "area": body.get("area", ""),
                    "requirements_json": json.dumps(body.get("requirements") or []),
                    "timebox_min": body.get("timebox_min"),
                    "tester_user_id": principal.user_id,
                    "started_at": now,
                }
            )
            diff.update(id=session_id, run_id=run_id)
            return self._session_doc(scoped, session_id)

    def _session_doc(self, scoped: ScopedStore, session_id: str) -> dict[str, Any]:
        session = scoped.session(session_id)
        if session is None:
            raise NotFound(f"no exploratory session {session_id!r}")
        return exploratory.summary(session, scoped.session_notes(session_id), self.clock())

    def get_session(self, principal: Principal, project: str, session_id: str) -> dict[str, Any]:
        return self._session_doc(self._scope(principal, project, READ), session_id)

    def list_sessions(
        self, principal: Principal, project: str, *, requirement: str | None = None
    ) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ)
        now = self.clock()
        return {
            "sessions": [
                {k: v for k, v in exploratory.summary(s, [], now).items() if k != "notes"}
                for s in scoped.sessions(requirement=requirement)
            ]
        }

    def add_session_note(
        self, principal: Principal, project: str, session_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Append a timestamped note (an attachment becomes an evidence link on the run)."""
        with self._write(
            principal,
            project,
            RECORD_MANUAL,
            "session.note",
            ("session", session_id),
            schema=("session-note", body),
        ) as (scoped, diff):
            session = scoped.session(session_id)
            if session is None:
                raise NotFound(f"no exploratory session {session_id!r}")
            if session["status"] != "active":
                raise Conflict(f"session {session_id} is {session['status']}")
            seq = scoped.add_session_note(
                session_id,
                {
                    "ts": utc_now(),
                    "kind": body["kind"],
                    "text": body["text"],
                    "attachment_uri": body.get("attachment_uri") or None,
                    "defect_key": body.get("defect_key") or None,
                    "author": principal.user_id,
                },
            )
            if body.get("attachment_uri"):
                scoped.add_evidence(
                    [
                        EvidenceRecord(
                            run_id=session["run_id"],
                            kind=_evidence_kind(body["attachment_uri"]),
                            uri=body["attachment_uri"],
                            nodeid=exploratory.SESSION_PREFIX + session_id,
                        )
                    ]
                )
            diff.update(seq=seq, kind=body["kind"], defect_key=body.get("defect_key"))
            return {"session_id": session_id, "seq": seq, "kind": body["kind"]}

    def end_session(
        self, principal: Principal, project: str, session_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """End (or abandon) a session once. An ended session records each linked
        requirement as ``recorded``; an abandoned one records nothing."""
        with self._write(
            principal,
            project,
            RECORD_MANUAL,
            "session.end",
            ("session", session_id),
            schema=("session-end", body),
        ) as (scoped, diff):
            session = scoped.session(session_id)
            if session is None:
                raise NotFound(f"no exploratory session {session_id!r}")
            if session["status"] != "active":
                raise Conflict(f"session {session_id} is already {session['status']}")
            status = "abandoned" if body.get("abandoned") else "ended"
            now = utc_now()
            scoped.end_session(session_id, status, now, body.get("debrief", ""))
            if status == "ended":
                scoped.add_verdicts(
                    VerdictRecord(
                        run_id=session["run_id"],
                        nodeid=exploratory.SESSION_PREFIX + session_id,
                        kind="coverage",
                        verdict="recorded",
                        subject=req,
                        detail=f"exploratory session: {session['charter'][:200]}",
                        recorded_at=now,
                    )
                    for req in session["requirements"]
                )
            run = self._run(scoped, session["run_id"])
            integrity = judge(
                scoped.final_results(run["run_id"]),
                scoped.run_verdicts(run["run_id"]),
                run.get("expected_count"),
                self.thresholds,
            )
            scoped.finish_run(
                run["run_id"],
                "finished" if status == "ended" else "interrupted",
                integrity["verdict"],
                now,
            )
            diff.update(status=status, requirements=len(session["requirements"]))
            return self._session_doc(scoped, session_id)

    # ------------------------------------------------------------------ members and tokens

    def list_members(self, principal: Principal, project: str) -> dict[str, Any]:
        scoped = self._scope(principal, project, MANAGE_MEMBERS)
        return {"members": scoped.members()}

    def _admins(self, scoped: ScopedStore) -> list[str]:
        return [m["user_id"] for m in scoped.members() if m["role"] == Role.ADMIN.value]

    def grant_member(
        self, principal: Principal, project: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Give a user (created on first grant) a role in the project, or change it."""
        with self._write(
            principal,
            project,
            MANAGE_MEMBERS,
            "member.grant",
            ("user", ""),
            schema=("member-grant", body),
        ) as (
            scoped,
            diff,
        ):
            user_id, role = body["user_id"], body["role"]
            previous = scoped.member(user_id)
            if (
                previous
                and previous["role"] == Role.ADMIN.value
                and role != Role.ADMIN.value
                and self._admins(scoped) == [user_id]
            ):
                raise Conflict("the project's last admin cannot be demoted")
            user = self.store.ensure_user(user_id, body.get("display_name", ""))
            if user["disabled"]:
                raise Conflict(f"user {user_id!r} is disabled")
            scoped.grant(user_id, role, principal.user_id)
            diff.update(id=user_id, role=role, previous=previous["role"] if previous else None)
            return {"user_id": user_id, "role": role, "project": project}

    def remove_member(self, principal: Principal, project: str, user_id: str) -> dict[str, Any]:
        """Remove a binding and revoke every token the user holds in the project."""
        with self._write(
            principal, project, MANAGE_MEMBERS, "member.revoke", ("user", user_id)
        ) as (
            scoped,
            diff,
        ):
            member = scoped.member(user_id)
            if member is None:
                raise NotFound(f"user {user_id!r} has no role in project {project!r}")
            if member["role"] == Role.ADMIN.value and self._admins(scoped) == [user_id]:
                raise Conflict("the project's last admin cannot be removed")
            scoped.remove_member(user_id)
            revoked = scoped.revoke_tokens(user_id=user_id)
            diff.update(role=member["role"], tokens_revoked=revoked)
            return {"user_id": user_id, "removed": True, "tokens_revoked": revoked}

    def list_tokens(self, principal: Principal, project: str) -> dict[str, Any]:
        scoped = self._scope(principal, project, MANAGE_TOKENS)
        return {"tokens": scoped.project_tokens()}

    def issue_token(
        self, principal: Principal, project: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """A new project token; the raw value is in this response and nowhere else."""
        with self._write(
            principal,
            project,
            MANAGE_TOKENS,
            "token.issue",
            ("token", ""),
            schema=("token-create", body),
        ) as (
            scoped,
            diff,
        ):
            raw, row = tokens.issue(
                self.store,
                user_id=body.get("user_id") or principal.user_id,
                project_id=project,
                role=body["role"],
                label=body.get("label", ""),
                ttl_days=body.get("ttl_days", tokens.DEFAULT_TTL_DAYS),
                now=self.clock(),
            )
            diff.update(
                id=row["id"], user_id=row["user_id"], role=row["role"], expires_at=row["expires_at"]
            )
            out = {k: v for k, v in row.items() if k != "project_id"}
            return {"token": raw, **out, "project": project}

    def revoke_token(self, principal: Principal, project: str, token_id: str) -> dict[str, Any]:
        with self._write(
            principal, project, MANAGE_TOKENS, "token.revoke", ("token", token_id)
        ) as (
            scoped,
            diff,
        ):
            if scoped.token(token_id) is None:
                raise NotFound(f"no token {token_id!r} in project {project!r}")
            revoked = scoped.revoke_tokens(token_id=token_id)
            if revoked:
                diff.update(revoked=True)
            return {"id": token_id, "revoked": True}

    # ------------------------------------------------------------------ audit log

    def audit_log(
        self,
        principal: Principal,
        project: str,
        *,
        action: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        cursor: str | None = None,
        limit: int | str | None = None,
    ) -> dict[str, Any]:
        scoped = self._scope(principal, project, READ_AUDIT)
        size = _limit(limit)
        rows = scoped.audit_page(
            action=action, actor=actor, since=since, after=_decode_cursor(cursor), limit=size + 1
        )
        out: dict[str, Any] = {"entries": [audit.public_row(r) for r in rows[:size]]}
        if len(rows) > size:
            out["next_cursor"] = _encode_cursor(rows[size - 1]["seq"])
        return out

    def audit_verify(self, principal: Principal, project: str) -> dict[str, Any]:
        return audit.verify_chain(self._scope(principal, project, READ_AUDIT)).to_json()

    def audit_head(self, principal: Principal, project: str) -> dict[str, Any]:
        return audit.head(self._scope(principal, project, READ_AUDIT))

    # ------------------------------------------------------------------ idempotency

    def idempotent(
        self,
        principal: Principal,
        project: str,
        key: str | None,
        endpoint: str,
        call: Callable[[], tuple[int, Any]],
    ) -> tuple[int, Any]:
        """Run ``call`` once per ``Idempotency-Key``; a repeat within 24 h replays the first
        response, a key reused on a different endpoint is a conflict."""
        if not key:
            return call()
        if len(key) > 200:
            raise BadRequest("Idempotency-Key is longer than 200 characters")
        scoped = self._scope(principal, project, READ)
        cutoff = (self.clock() - IDEMPOTENCY_TTL).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        scoped.purge_responses(cutoff)
        stored = scoped.stored_response(key)
        if stored is not None:
            if stored["endpoint"] != endpoint:
                raise Conflict("Idempotency-Key was already used for a different request")
            return int(stored["status"]), json.loads(stored["response_json"])
        status, response = call()
        if status < 400:
            scoped.store_response(key, endpoint, status, response)
        return status, response


__all__ = [
    "MANAGE_MEMBERS",
    "MANAGE_PROJECT",
    "MANAGE_TOKENS",
    "PUSH_RESULTS",
    "READ",
    "READ_AUDIT",
    "RECORD_MANUAL",
    "TRIGGER_RUN",
    "PlatformService",
]
