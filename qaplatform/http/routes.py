"""The REST surface as data, plus a framework-free request pipeline.

:data:`ROUTES` is the single list of endpoints: the web adapter mounts exactly these, the
OpenAPI document is generated from them, and the contract test compares the two. The
pipeline :func:`handle` (context, auth, body decoding, size limits, idempotency, error ->
problem+json) is plain Python, so the whole HTTP behaviour short of the socket is testable
without a web framework installed.

Handlers only call :class:`~qaplatform.service.PlatformService`; nothing under
``qaplatform/http/`` imports the store (asserted by a source scan).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from core.logger import logger
from qaplatform import context, schemas
from qaplatform.auth.tokens import Principal, bearer
from qaplatform.errors import BadRequest, NotFound, PlatformError, TooLarge

PREFIX = "/api/v1"
JSON_LIMIT = 10_000_000
JUNIT_LIMIT = 50_000_000
PROBLEM_TYPE = "application/problem+json"


@dataclass
class Call:
    """One decoded request, as a handler sees it."""

    params: dict[str, str]
    body: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)


Handler = Callable[[Any, Principal | None, Call], tuple[int, Any]]


@dataclass(frozen=True)
class Route:
    method: str
    path: str  # relative to PREFIX, with {name} placeholders
    name: str
    handler: Handler
    summary: str
    auth: bool = True
    body: str | None = None  # None | "json" | "junit" | "verdicts"
    request_schema: str | None = None
    response_schema: str | None = None
    status: int = 200
    query: tuple[str, ...] = ()
    max_bytes: int = JSON_LIMIT
    idempotent_post: bool = False  # honours Idempotency-Key

    @property
    def full_path(self) -> str:
        return PREFIX + self.path

    def match(self, method: str, path: str) -> dict[str, str] | None:
        if method.upper() != self.method:
            return None
        want = [p for p in self.path.strip("/").split("/") if p]
        got = [p for p in path.strip("/").split("/") if p]
        if len(want) != len(got):
            return None
        bound: dict[str, str] = {}
        for w, g in zip(want, got, strict=True):
            if w.startswith("{") and w.endswith("}"):
                bound[w[1:-1]] = g
            elif w != g:
                return None
        return bound


# ---------------------------------------------------------------------- handlers


def _q(call: Call, name: str) -> str | None:
    value = call.params.get(name)
    return value if value not in (None, "") else None


def h_health(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.health()


def h_ready(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    doc = svc.ready()
    return (200 if doc["status"] == "ready" else 503), doc


def h_version(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    build = os.environ.get("QAP_BUILD_SHA") or os.environ.get("GITHUB_SHA") or "unknown"
    return 200, {
        "api": schemas.API_VERSION,
        "schema_version": schemas.SCHEMA_VERSION,
        "build": build,
    }


def h_openapi(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    from qaplatform.http.openapi import build_openapi

    return 200, build_openapi()


def h_schema(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    try:
        return 200, schemas.load(call.params["name"])
    except FileNotFoundError as exc:
        raise NotFound(str(exc)) from exc


def h_me(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.me(p)


def h_create_run(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    created, summary = svc.create_run(p, call.params["project"], call.body)
    return (201 if created else 200), summary


def h_list_runs(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.list_runs(
        p,
        call.params["project"],
        status=_q(call, "status"),
        branch=_q(call, "branch"),
        source=_q(call, "source"),
        since=_q(call, "since"),
        cursor=_q(call, "cursor"),
        limit=_q(call, "limit"),
    )


def h_get_run(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.get_run(p, call.params["project"], call.params["run_id"])


def h_append_results(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.append_results(p, call.params["project"], call.params["run_id"], call.body)


def h_append_junit(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.append_junit(p, call.params["project"], call.params["run_id"], call.body)


def h_append_verdicts(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.append_verdicts(p, call.params["project"], call.params["run_id"], call.body)


def h_add_evidence(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.add_evidence(p, call.params["project"], call.params["run_id"], call.body)


def h_run_evidence(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.run_evidence(p, call.params["project"], call.params["run_id"])


def h_finish_run(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.finish_run(p, call.params["project"], call.params["run_id"], call.body)


def h_run_results(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.run_results(
        p,
        call.params["project"],
        call.params["run_id"],
        outcome=_q(call, "outcome"),
        attempts=_q(call, "attempts"),
        cursor=_q(call, "cursor"),
        limit=_q(call, "limit"),
    )


def h_verdicts(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.list_verdicts(
        p,
        call.params["project"],
        run_id=_q(call, "run_id"),
        ticket=_q(call, "ticket"),
        kind=_q(call, "kind"),
        cursor=_q(call, "cursor"),
        limit=_q(call, "limit"),
    )


def h_case_history(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    test_id = _q(call, "test_id")
    if not test_id:
        raise BadRequest("test_id is required")
    return 200, svc.case_history(p, call.params["project"], test_id, limit=_q(call, "limit"))


def h_traceability(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.traceability(
        p, call.params["project"], requirement=_q(call, "requirement"), release=_q(call, "release")
    )


def h_requirement(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.requirement(p, call.params["project"], call.params["requirement"])


def h_release_verdict(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.release_verdict(p, call.params["project"], call.params["release"])


def h_request_run(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 202, svc.request_run(p, call.params["project"], call.body)


def h_get_run_request(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.get_run_request(p, call.params["project"], call.params["request_id"])


def h_audit(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.audit_log(
        p,
        call.params["project"],
        action=_q(call, "action"),
        actor=_q(call, "actor"),
        since=_q(call, "since"),
        cursor=_q(call, "cursor"),
        limit=_q(call, "limit"),
    )


def h_audit_verify(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.audit_verify(p, call.params["project"])


def h_audit_head(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.audit_head(p, call.params["project"])


def h_members(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.list_members(p, call.params["project"])


def h_grant(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.grant_member(p, call.params["project"], call.body)


def h_remove_member(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.remove_member(p, call.params["project"], call.params["user_id"])


def h_tokens(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.list_tokens(p, call.params["project"])


def h_issue_token(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 201, svc.issue_token(p, call.params["project"], call.body)


def h_revoke_token(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.revoke_token(p, call.params["project"], call.params["token_id"])


def h_projects(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.list_projects(p)


def h_create_project(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 201, svc.create_project(p, call.body)


def h_project(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.get_project(p, call.params["project"])


def h_update_project(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.update_project(p, call.params["project"], call.body)


def h_start_manual(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 201, svc.start_manual_run(p, call.params["project"], call.body)


def h_manual_run(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.get_manual_run(p, call.params["project"], call.params["run_id"])


def h_manual_step(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.record_manual_step(
        p, call.params["project"], call.params["execution_id"], call.params["step_no"], call.body
    )


def h_manual_finish(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.finish_manual_execution(p, call.params["project"], call.params["execution_id"])


def h_start_session(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 201, svc.start_session(p, call.params["project"], call.body)


def h_sessions(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.list_sessions(p, call.params["project"], requirement=_q(call, "requirement"))


def h_session(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.get_session(p, call.params["project"], call.params["session_id"])


def h_session_note(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.add_session_note(
        p, call.params["project"], call.params["session_id"], call.body
    )


def h_session_end(svc: Any, p: Principal | None, call: Call) -> tuple[int, Any]:
    return 200, svc.end_session(p, call.params["project"], call.params["session_id"], call.body)


RUN = "/projects/{project}/runs/{run_id}"
ROUTES: list[Route] = [
    Route("GET", "/health", "health", h_health, "Liveness", auth=False),
    Route("GET", "/ready", "ready", h_ready, "Store reachable and migrated", auth=False),
    Route("GET", "/version", "version", h_version, "API, schema and build versions", auth=False),
    Route("GET", "/openapi.json", "openapi", h_openapi, "This contract as OpenAPI", auth=False),
    Route("GET", "/schemas/{name}", "schema", h_schema, "One JSON Schema", auth=False),
    Route("GET", "/me", "me", h_me, "The calling principal"),
    Route("GET", "/projects", "list_projects", h_projects, "Projects visible to the token"),
    Route(
        "POST",
        "/projects",
        "create_project",
        h_create_project,
        "Create a project (instance admin)",
        body="json",
        request_schema="project-create",
        response_schema="project",
        status=201,
    ),
    Route(
        "GET",
        "/projects/{project}",
        "get_project",
        h_project,
        "One project",
        response_schema="project",
    ),
    Route(
        "PATCH",
        "/projects/{project}",
        "update_project",
        h_update_project,
        "Rename, change settings, archive or unarchive",
        body="json",
        request_schema="project-update",
        response_schema="project",
    ),
    Route(
        "POST",
        "/projects/{project}/runs",
        "create_run",
        h_create_run,
        "Create a run, or return the run with this external_id",
        body="json",
        request_schema="run-create",
        response_schema="run-summary",
        status=201,
    ),
    Route(
        "GET",
        "/projects/{project}/runs",
        "list_runs",
        h_list_runs,
        "List runs, newest first",
        query=("status", "branch", "source", "since", "cursor", "limit"),
    ),
    Route("GET", RUN, "get_run", h_get_run, "Run summary", response_schema="run-summary"),
    Route(
        "POST",
        RUN + "/results",
        "append_results",
        h_append_results,
        "Append a batch of results (at most 1000)",
        body="json",
        request_schema="results-batch",
    ),
    Route(
        "POST",
        RUN + "/junit",
        "append_junit",
        h_append_junit,
        "Append results from a JUnit XML document",
        body="junit",
        max_bytes=JUNIT_LIMIT,
    ),
    Route(
        "POST",
        RUN + "/verdicts",
        "append_verdicts",
        h_append_verdicts,
        "Append verdict rows (JSON array, or JSONL with application/x-ndjson)",
        body="verdicts",
        request_schema="verdicts-batch",
        idempotent_post=True,
    ),
    Route(
        "POST",
        RUN + "/evidence",
        "add_evidence",
        h_add_evidence,
        "Attach evidence links",
        body="json",
        request_schema="evidence-batch",
        idempotent_post=True,
    ),
    Route("GET", RUN + "/evidence", "run_evidence", h_run_evidence, "Evidence links of a run"),
    Route(
        "POST",
        RUN + "/finish",
        "finish_run",
        h_finish_run,
        "Finish the run and compute its integrity verdict",
        body="json",
        request_schema="run-finish",
        response_schema="run-summary",
    ),
    Route(
        "GET",
        RUN + "/results",
        "run_results",
        h_run_results,
        "Results of a run (final attempts; attempts=all for every attempt)",
        query=("outcome", "attempts", "cursor", "limit"),
    ),
    Route(
        "GET",
        "/projects/{project}/verdicts",
        "list_verdicts",
        h_verdicts,
        "Verdict rows",
        query=("run_id", "ticket", "kind", "cursor", "limit"),
    ),
    Route(
        "GET",
        "/projects/{project}/history",
        "case_history",
        h_case_history,
        "Outcome history of one test",
        query=("test_id", "limit"),
    ),
    Route(
        "GET",
        "/projects/{project}/traceability",
        "traceability",
        h_traceability,
        "Requirement - test - latest verdict matrix",
        response_schema="traceability",
        query=("requirement", "release"),
    ),
    Route(
        "GET",
        "/projects/{project}/requirements/{requirement}",
        "requirement",
        h_requirement,
        "Status of one requirement",
    ),
    Route(
        "GET",
        "/projects/{project}/releases/{release}/verdict",
        "release_verdict",
        h_release_verdict,
        "Release verdict",
        response_schema="release-verdict",
    ),
    Route(
        "POST",
        "/projects/{project}/run-requests",
        "request_run",
        h_request_run,
        "Ask for a run; handed to the configured dispatcher",
        body="json",
        request_schema="run-request",
        response_schema="run-request-status",
        status=202,
        idempotent_post=True,
    ),
    Route(
        "GET",
        "/projects/{project}/run-requests/{request_id}",
        "get_run_request",
        h_get_run_request,
        "Poll a run request",
        response_schema="run-request-status",
    ),
    Route(
        "GET",
        "/projects/{project}/audit",
        "audit_log",
        h_audit,
        "Audit log entries, oldest first",
        query=("action", "actor", "since", "cursor", "limit"),
    ),
    Route(
        "GET",
        "/projects/{project}/audit/verify",
        "audit_verify",
        h_audit_verify,
        "Recompute the audit hash chain; names the first broken row",
    ),
    Route(
        "GET",
        "/projects/{project}/audit/head",
        "audit_head",
        h_audit_head,
        "The current audit chain head (anchor it outside the database)",
    ),
    Route(
        "POST",
        "/projects/{project}/manual-runs",
        "start_manual_run",
        h_start_manual,
        "Start a manual run from spec markdown or a case list",
        body="json",
        request_schema="manual-run-create",
        status=201,
    ),
    Route(
        "GET",
        "/projects/{project}/manual-runs/{run_id}",
        "get_manual_run",
        h_manual_run,
        "A manual run: summary plus every execution and its steps",
    ),
    Route(
        "PUT",
        "/projects/{project}/manual-executions/{execution_id}/steps/{step_no}",
        "record_manual_step",
        h_manual_step,
        "Record one step: pass, fail, blocked or na",
        body="json",
        request_schema="manual-step",
    ),
    Route(
        "POST",
        "/projects/{project}/manual-executions/{execution_id}/finish",
        "finish_manual_execution",
        h_manual_finish,
        "Finish an execution: its steps become one result",
    ),
    Route(
        "POST",
        "/projects/{project}/sessions",
        "start_session",
        h_start_session,
        "Start an exploratory session",
        body="json",
        request_schema="session-start",
        status=201,
    ),
    Route(
        "GET",
        "/projects/{project}/sessions",
        "list_sessions",
        h_sessions,
        "Exploratory sessions, newest first",
        query=("requirement",),
    ),
    Route(
        "GET",
        "/projects/{project}/sessions/{session_id}",
        "get_session",
        h_session,
        "One session: charter, notes, bugs, debrief, timebox",
    ),
    Route(
        "POST",
        "/projects/{project}/sessions/{session_id}/notes",
        "add_session_note",
        h_session_note,
        "Add a note (observation, question, bug, idea)",
        body="json",
        request_schema="session-note",
    ),
    Route(
        "POST",
        "/projects/{project}/sessions/{session_id}/end",
        "end_session",
        h_session_end,
        "End or abandon the session",
        body="json",
        request_schema="session-end",
    ),
    Route("GET", "/projects/{project}/members", "list_members", h_members, "Role bindings"),
    Route(
        "POST",
        "/projects/{project}/members",
        "grant_member",
        h_grant,
        "Grant or change a user's role",
        body="json",
        request_schema="member-grant",
    ),
    Route(
        "DELETE",
        "/projects/{project}/members/{user_id}",
        "remove_member",
        h_remove_member,
        "Remove a member and revoke their tokens in the project",
    ),
    Route("GET", "/projects/{project}/tokens", "list_tokens", h_tokens, "Tokens (never secrets)"),
    Route(
        "POST",
        "/projects/{project}/tokens",
        "issue_token",
        h_issue_token,
        "Issue a token; the raw value is returned once",
        body="json",
        request_schema="token-create",
        response_schema="token-issued",
        status=201,
    ),
    Route(
        "DELETE",
        "/projects/{project}/tokens/{token_id}",
        "revoke_token",
        h_revoke_token,
        "Revoke a token",
    ),
]


def add_routes(routes: list[Route]) -> None:
    """Later features append their routes here, so one table describes the whole API."""
    names = {r.name for r in ROUTES}
    for route in routes:
        if route.name not in names:
            ROUTES.append(route)
            names.add(route.name)


def find(method: str, path: str) -> tuple[Route, dict[str, str]] | None:
    """The route for ``path`` (relative to the API prefix) and its bound path params."""
    for route in ROUTES:
        bound = route.match(method, path)
        if bound is not None:
            return route, bound
    return None


# ---------------------------------------------------------------------- pipeline


def _lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()}


def decode_body(route: Route, content_type: str, raw: bytes) -> Any:
    if len(raw) > route.max_bytes:
        raise TooLarge(f"body is {len(raw)} bytes, the limit is {route.max_bytes}")
    if route.body is None:
        return None
    if route.body == "junit":
        return raw
    ctype = content_type.split(";")[0].strip().lower()
    if route.body == "verdicts" and ctype in ("application/x-ndjson", "application/jsonl"):
        return raw.decode("utf-8", errors="replace")
    if not raw.strip():
        raise BadRequest("a JSON body is required")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadRequest(f"body is not JSON: {exc}") from exc


@dataclass
class Response:
    status: int
    payload: Any
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)


def handle(
    service: Any,
    method: str,
    path: str,
    *,
    query: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
    client_ip: str | None = None,
) -> Response:
    """Serve one request whose ``path`` is relative to :data:`PREFIX`."""
    hdrs = _lower(headers or {})
    with context.request(hdrs.get("x-request-id") or None, client_ip) as ctx:
        instance = PREFIX + "/" + path.strip("/")
        try:
            found = find(method, path)
            if found is None:
                raise NotFound(f"{method.upper()} {instance}")
            route, bound = found
            principal = service.authenticate(bearer(hdrs)) if route.auth else None
            call = Call(
                params={**dict(query or {}), **bound},
                body=decode_body(route, hdrs.get("content-type", ""), body),
                headers=hdrs,
            )
            if route.idempotent_post and principal is not None and "project" in bound:
                status, payload = service.idempotent(
                    principal,
                    bound["project"],
                    hdrs.get("idempotency-key"),
                    f"{route.method} {route.path}",
                    lambda: route.handler(service, principal, call),
                )
            else:
                status, payload = route.handler(service, principal, call)
            return Response(status, payload, headers={"X-Request-Id": ctx.request_id})
        except PlatformError as exc:
            out = Response(
                exc.status,
                exc.to_problem(instance=instance, request_id=ctx.request_id),
                PROBLEM_TYPE,
                {"X-Request-Id": ctx.request_id},
            )
            if exc.status == 401:
                out.headers["WWW-Authenticate"] = 'Bearer realm="qaplatform"'
            return out
        except Exception:  # a bug: answer a problem document, never a stack trace
            logger.exception("[qaplatform] %s %s failed (%s)", method, instance, ctx.request_id)
            problem = PlatformError("unexpected server error").to_problem(
                instance=instance, request_id=ctx.request_id
            )
            return Response(500, problem, PROBLEM_TYPE, {"X-Request-Id": ctx.request_id})


__all__ = [
    "JSON_LIMIT",
    "JUNIT_LIMIT",
    "PREFIX",
    "ROUTES",
    "Call",
    "Response",
    "Route",
    "add_routes",
    "find",
    "handle",
]
