"""UI pages as data plus a framework-free request pipeline.

Each page handler calls :class:`~qaplatform.service.PlatformService` (the same reads the
REST API exposes, so the UI cannot show a number the API would not) and returns the
template name and its context. :func:`ui_handle` does sign-in, the session cookie, CSRF
checks on forms and error pages. Rendering happens in :mod:`qaplatform.http.ui` with
Jinja2; nothing here needs it.

Sign-in is by API token (the only identity the platform has): the token is pasted once into
the sign-in form and kept in an ``HttpOnly``, ``SameSite=Strict`` cookie scoped to ``/ui``.
Every form that changes something carries a CSRF value derived from that token.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from qaplatform import context
from qaplatform.auth.tokens import Principal
from qaplatform.errors import Forbidden, NotConfigured, NotFound, PlatformError, Unauthorized
from qaplatform.http.ui import views
from qaplatform.rbac import allows

COOKIE = "qap_session"
UI = "/ui"
PAGE_SIZE = 200


@dataclass
class UiRequest:
    params: dict[str, str]
    form: dict[str, str] = field(default_factory=dict)
    token: str | None = None


@dataclass
class UiResponse:
    status: int = 200
    template: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    redirect: str | None = None
    set_token: str | None = None  # set the session cookie to this value
    clear_token: bool = False


UiHandler = Callable[[Any, Principal, UiRequest], UiResponse]


@dataclass(frozen=True)
class UiRoute:
    method: str
    path: str
    name: str
    handler: UiHandler
    writes: bool = False  # POST forms: CSRF-checked

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


def csrf_token(raw_token: str) -> str:
    """A per-session form token: an HMAC of a fixed label under the session token's hash."""
    key = hashlib.sha256(raw_token.encode("utf-8")).digest()
    return hmac.new(key, b"qaplatform-ui-csrf", hashlib.sha256).hexdigest()[:32]


def _page(template: str, **ctx: Any) -> UiResponse:
    return UiResponse(200, template, ctx)


def _can(principal: Principal, permission: str) -> bool:
    return allows(principal.role, permission)


# ---------------------------------------------------------------------- read pages


def projects(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    return _page("projects.html", projects=svc.list_projects(p)["projects"])


def project_home(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    svc.get_project(p, req.params["project"])
    return UiResponse(303, redirect=f"{UI}/{req.params['project']}/runs")


def runs(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    project = req.params["project"]
    page = svc.list_runs(
        p,
        project,
        status=req.params.get("status") or None,
        source=req.params.get("source") or None,
        cursor=req.params.get("cursor") or None,
        limit=50,
    )
    return _page(
        "runs.html",
        runs=views.runs_page(page["runs"]),
        next_cursor=page.get("next_cursor"),
        status=req.params.get("status", ""),
    )


def run_detail(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    project, run_id = req.params["project"], req.params["run_id"]
    summary = svc.get_run(p, project, run_id)
    results = svc.run_results(p, project, run_id, limit=PAGE_SIZE)
    verdicts = svc.list_verdicts(p, project, run_id=run_id, limit=PAGE_SIZE)
    evidence = svc.run_evidence(p, project, run_id)
    ctx = views.run_page(summary, results["results"], verdicts["verdicts"], evidence["links"])
    ctx["truncated"] = bool(results.get("next_cursor") or verdicts.get("next_cursor"))
    return _page("run.html", **ctx)


def history(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    test_id = req.params.get("test_id", "")
    rows = svc.case_history(p, req.params["project"], test_id)["history"] if test_id else []
    return _page("history.html", test_id=test_id, history=rows)


def requirements(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    release = req.params.get("release") or None
    report = svc.traceability(p, req.params["project"], release=release)
    return _page("requirements.html", **views.requirements_page(report))


def release(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    name = req.params["release"]
    try:
        verdict = svc.release_verdict(p, req.params["project"], name)
    except NotConfigured as exc:
        return _page("release.html", release=name, verdict=None, reason=exc.detail)
    return _page("release.html", release=name, verdict=verdict, reason="")


def audit_log(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    project = req.params["project"]
    page = svc.audit_log(
        p,
        project,
        action=req.params.get("action") or None,
        actor=req.params.get("actor") or None,
        cursor=req.params.get("cursor") or None,
        limit=100,
    )
    chain = svc.audit_verify(p, project)
    return _page(
        "audit.html",
        entries=page["entries"],
        next_cursor=page.get("next_cursor"),
        chain=chain,
        action=req.params.get("action", ""),
        actor=req.params.get("actor", ""),
    )


# ---------------------------------------------------------------------- manual runner


def manual_runs(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    page = svc.list_runs(p, req.params["project"], source="manual", limit=50)
    return _page(
        "manual_runs.html",
        runs=views.runs_page(page["runs"]),
        can_record=_can(p, "record_manual"),
    )


def manual_start(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    project = req.params["project"]
    body = {
        "schema_version": "1.0",
        "name": req.form.get("name", "").strip() or "manual run",
        "spec": req.form.get("spec", ""),
    }
    if req.form.get("release", "").strip():
        body["release"] = req.form["release"].strip()
    doc = svc.start_manual_run(p, project, body)
    return UiResponse(303, redirect=f"{UI}/{project}/manual/{doc['run']['run_id']}")


def manual_run(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    doc = svc.get_manual_run(p, req.params["project"], req.params["run_id"])
    return _page(
        "manual_run.html",
        run=doc["run"],
        headline=views.headline(doc["run"]),
        executions=doc["executions"],
        can_record=_can(p, "record_manual") and doc["run"]["status"] == "open",
    )


def manual_execution(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    doc = svc.get_manual_run(p, req.params["project"], req.params["run_id"])
    ex = next((e for e in doc["executions"] if e["id"] == req.params["execution_id"]), None)
    if ex is None:
        raise NotFound("no such execution in this run")
    recorded = {s["step_no"]: s for s in ex["step_results"]}
    steps = [
        {"no": n, "text": text, "result": recorded.get(n)} for n, text in enumerate(ex["steps"], 1)
    ]
    return _page(
        "manual_execution.html",
        run=doc["run"],
        execution=ex,
        steps=steps,
        next_step=ex["missing_steps"][0] if ex["missing_steps"] else None,
        can_record=_can(p, "record_manual")
        and doc["run"]["status"] == "open"
        and ex["status"] == "in_progress",
    )


def manual_step(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    project, run_id, ex_id = (req.params[k] for k in ("project", "run_id", "execution_id"))
    body = {
        "schema_version": "1.0",
        "status": req.form.get("status", ""),
        "actual": req.form.get("actual", ""),
    }
    if req.form.get("evidence_uri", "").strip():
        body["evidence_uri"] = req.form["evidence_uri"].strip()
    svc.record_manual_step(p, project, ex_id, req.params["step_no"], body)
    return UiResponse(303, redirect=f"{UI}/{project}/manual/{run_id}/{ex_id}")


def manual_finish_execution(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    project, run_id, ex_id = (req.params[k] for k in ("project", "run_id", "execution_id"))
    svc.finish_manual_execution(p, project, ex_id)
    return UiResponse(303, redirect=f"{UI}/{project}/manual/{run_id}")


def manual_finish_run(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    project, run_id = req.params["project"], req.params["run_id"]
    svc.finish_run(p, project, run_id, {"schema_version": "1.0"})
    return UiResponse(303, redirect=f"{UI}/{project}/runs/{run_id}")


# ---------------------------------------------------------------------- exploratory


def sessions(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    doc = svc.list_sessions(p, req.params["project"])
    return _page("sessions.html", sessions=doc["sessions"], can_record=_can(p, "record_manual"))


def session_start(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    project = req.params["project"]
    body: dict[str, Any] = {"schema_version": "1.0", "charter": req.form.get("charter", "")}
    if req.form.get("area", "").strip():
        body["area"] = req.form["area"].strip()
    reqs = [r.strip() for r in req.form.get("requirements", "").replace(",", " ").split()]
    if reqs:
        body["requirements"] = reqs
    if req.form.get("timebox_min", "").strip():
        try:
            body["timebox_min"] = int(req.form["timebox_min"])
        except ValueError:
            body["timebox_min"] = req.form["timebox_min"]  # the schema check names it
    doc = svc.start_session(p, project, body)
    return UiResponse(303, redirect=f"{UI}/{project}/sessions/{doc['id']}")


def session_page(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    doc = svc.get_session(p, req.params["project"], req.params["session_id"])
    return _page(
        "session.html",
        session=doc,
        can_record=_can(p, "record_manual") and doc["status"] == "active",
    )


def session_note(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    project, sid = req.params["project"], req.params["session_id"]
    body = {
        "schema_version": "1.0",
        "kind": req.form.get("kind", ""),
        "text": req.form.get("text", ""),
    }
    for key in ("attachment_uri", "defect_key"):
        if req.form.get(key, "").strip():
            body[key] = req.form[key].strip()
    svc.add_session_note(p, project, sid, body)
    return UiResponse(303, redirect=f"{UI}/{project}/sessions/{sid}")


def session_end(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    project, sid = req.params["project"], req.params["session_id"]
    svc.end_session(
        p,
        project,
        sid,
        {
            "schema_version": "1.0",
            "debrief": req.form.get("debrief", ""),
            "abandoned": req.form.get("abandoned") == "yes",
        },
    )
    return UiResponse(303, redirect=f"{UI}/{project}/sessions/{sid}")


# ---------------------------------------------------------------------- session


def login_form(svc: Any, p: Principal | None, req: UiRequest) -> UiResponse:
    return _page("login.html", error="")


def login(svc: Any, p: Principal | None, req: UiRequest) -> UiResponse:
    raw = (req.form.get("token") or "").strip()
    try:
        svc.authenticate(raw)
    except Unauthorized:
        return UiResponse(401, "login.html", {"error": "That token is not valid."})
    return UiResponse(303, redirect=f"{UI}/", set_token=raw)


def logout(svc: Any, p: Principal, req: UiRequest) -> UiResponse:
    return UiResponse(303, redirect=f"{UI}/login", clear_token=True)


PUBLIC = {"login_form", "login"}

UI_ROUTES: list[UiRoute] = [
    UiRoute("GET", "/login", "login_form", login_form),  # type: ignore[arg-type]
    UiRoute("POST", "/login", "login", login),  # type: ignore[arg-type]
    UiRoute("POST", "/logout", "logout", logout, writes=True),
    UiRoute("GET", "/", "projects", projects),
    UiRoute("GET", "/{project}", "project_home", project_home),
    UiRoute("GET", "/{project}/runs", "runs", runs),
    UiRoute("GET", "/{project}/runs/{run_id}", "run", run_detail),
    UiRoute("GET", "/{project}/history", "history", history),
    UiRoute("GET", "/{project}/requirements", "requirements", requirements),
    UiRoute("GET", "/{project}/releases/{release}", "release", release),
    UiRoute("GET", "/{project}/audit", "audit", audit_log),
    UiRoute("GET", "/{project}/manual", "manual_runs", manual_runs),
    UiRoute("POST", "/{project}/manual", "manual_start", manual_start, writes=True),
    UiRoute("GET", "/{project}/manual/{run_id}", "manual_run", manual_run),
    UiRoute(
        "POST",
        "/{project}/manual/{run_id}/finish",
        "manual_finish_run",
        manual_finish_run,
        writes=True,
    ),
    UiRoute(
        "GET", "/{project}/manual/{run_id}/{execution_id}", "manual_execution", manual_execution
    ),
    UiRoute(
        "POST",
        "/{project}/manual/{run_id}/{execution_id}/steps/{step_no}",
        "manual_step",
        manual_step,
        writes=True,
    ),
    UiRoute(
        "POST",
        "/{project}/manual/{run_id}/{execution_id}/finish",
        "manual_finish_execution",
        manual_finish_execution,
        writes=True,
    ),
    UiRoute("GET", "/{project}/sessions", "sessions", sessions),
    UiRoute("POST", "/{project}/sessions", "session_start", session_start, writes=True),
    UiRoute("GET", "/{project}/sessions/{session_id}", "session", session_page),
    UiRoute(
        "POST",
        "/{project}/sessions/{session_id}/notes",
        "session_note",
        session_note,
        writes=True,
    ),
    UiRoute(
        "POST", "/{project}/sessions/{session_id}/end", "session_end", session_end, writes=True
    ),
]


def add_ui_routes(routes: list[UiRoute]) -> None:
    names = {r.name for r in UI_ROUTES}
    for route in routes:
        if route.name not in names:
            UI_ROUTES.append(route)
            names.add(route.name)


def _common(p: Principal | None, req: UiRequest, project: str | None) -> dict[str, Any]:
    nav = {}
    if p is not None and project:
        nav = {"audit": _can(p, "read_audit"), "manual": True, "sessions": True}
    return {
        "me": p.to_json() if p else None,
        "project": project,
        "csrf": csrf_token(req.token) if req.token else "",
        "nav": nav,
        "q": quote,
    }


def ui_handle(
    service: Any,
    method: str,
    path: str,
    *,
    query: Mapping[str, str] | None = None,
    form: Mapping[str, str] | None = None,
    token: str | None = None,
) -> UiResponse:
    """Serve one UI request whose ``path`` is relative to ``/ui``."""
    with context.request():
        found = None
        for route in UI_ROUTES:
            bound = route.match(method, path or "/")
            if bound is not None:
                found = (route, bound)
                break
        req = UiRequest(params={**dict(query or {})}, form=dict(form or {}), token=token)
        if found is None:
            return _error(404, "Page not found.", None, req)
        route, bound = found
        req.params.update(bound)
        project = bound.get("project")
        principal: Principal | None = None
        if route.name not in PUBLIC:
            try:
                principal = service.authenticate(token)
            except Unauthorized:
                return UiResponse(303, redirect=f"{UI}/login", clear_token=bool(token))
        if route.writes and not (
            token and hmac.compare_digest(req.form.get("csrf", ""), csrf_token(token))
        ):
            return _error(403, "The form expired or did not come from this site.", principal, req)
        try:
            resp = route.handler(service, principal, req)  # type: ignore[arg-type]
        except NotFound as exc:
            return _error(404, exc.detail or "Not found.", principal, req)
        except Forbidden as exc:
            return _error(403, exc.detail or "Not allowed.", principal, req)
        except PlatformError as exc:
            return _error(exc.status, exc.detail or exc.title, principal, req)
        if resp.template:
            resp.context = {**_common(principal, req, project), **resp.context}
        return resp


def _error(status: int, message: str, p: Principal | None, req: UiRequest) -> UiResponse:
    ctx = {**_common(p, req, req.params.get("project")), "status": status, "message": message}
    return UiResponse(status, "error.html", ctx)


__all__ = ["COOKIE", "UI", "UI_ROUTES", "UiResponse", "add_ui_routes", "csrf_token", "ui_handle"]
