"""Framework-agnostic REST handlers for traceability (mounted under ``/api/v1``).

Same contract as ``results.api``: ``handler(store, params, body) -> dict``; errors raise
:class:`results.api.ApiError`. :data:`ROUTES` lists ``(method, path template, handler)``;
importing this module also adds them to ``results.api.ROUTES``, so
``results.api.dispatch`` serves both tables.
"""

from __future__ import annotations

from typing import Any

from results.api import ApiError, Handler, add_routes
from traceability import store as trace_store
from traceability.requirements import Namespace

ROUTES: list[tuple[str, str, Handler]] = []


def route(method: str, path: str):
    def wrap(func: Handler) -> Handler:
        ROUTES.append((method, path, func))
        add_routes([(method, path, func)])
        return func

    return wrap


def _case(store: Any, params: dict[str, Any]) -> str:
    case_id = str(params.get("case_id") or "")
    if not store.query("case_get", case_id=case_id):
        raise ApiError(404, "Unknown case", f"no case {case_id!r}")
    return case_id


@route("GET", "/cases/{case_id}/requirements")
def get_case_requirements(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    case_id = _case(store, params)
    return {"case_id": case_id, "requirements": trace_store.case_requirements(store, case_id)}


@route("PUT", "/cases/{case_id}/requirements")
def put_case_requirements(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """For frameworks that do not push links per result: set a case's external links."""
    case_id = _case(store, params)
    data = body if isinstance(body, dict) else {}
    if data.get("origin", "external") != "external":
        raise ApiError(400, "Invalid origin", "only origin 'external' can be set over REST")
    ids = data.get("requirements")
    if not isinstance(ids, list):
        raise ApiError(400, "Invalid body", "requirements must be a list of ids")
    ns = Namespace.from_env()
    bad = [i for i in ids if not ns.is_requirement_id(i)]
    if bad:
        raise ApiError(400, "Invalid requirement id", ", ".join(map(str, bad)))
    trace_store.replace_case_requirements(store, case_id, ids)
    return get_case_requirements(store, params, None)


def _registry(store: Any):
    from traceability.requirements import Registry
    from traceability.store import load_registry

    reg = load_registry(store)
    return reg if len(reg) else Registry.load()


def _scope(params: dict[str, Any]):
    from traceability.evidence import Scope

    runs = tuple(r for r in str(params.get("runs") or "").split(",") if r)
    last = params.get("last")
    reqs = tuple(r for r in str(params.get("requirements") or "").split(",") if r)
    return Scope(
        run_ids=runs,
        branch=params.get("branch") or None,
        last=int(last) if last not in (None, "") else None,
        release=params.get("release") or None,
        requirements=reqs,
    )


@route("GET", "/traceability/matrix")
def get_matrix(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """Compute the matrix for an explicit scope; an unscoped request is a 400."""
    from traceability.evidence import StoreResultSource
    from traceability.matrix import build_matrix, matrix_json, render_markdown

    scope = _scope(params)
    try:
        source = StoreResultSource(store, scope)
    except ValueError as exc:
        raise ApiError(400, "Unscoped matrix", str(exc)) from exc
    matrix = build_matrix(_registry(store), None, source, scope)
    if params.get("format") == "md":
        return {"format": "md", "body": render_markdown(matrix)}
    return matrix_json(matrix)


@route("GET", "/traceability/requirements/{requirement_id}")
def get_requirement_row(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """One requirement's row; scope parameters as for the matrix."""
    req_id = str(params.get("requirement_id"))
    registry = _registry(store)
    if registry.status(req_id) == "unknown":
        raise ApiError(404, "Unknown requirement", req_id)
    data = get_matrix(store, {**params, "requirements": req_id, "format": "json"}, None)
    row = data["rows"][0]
    req = registry.get(req_id)
    row["requirement"] = req.to_json() if req else None
    return row


def _plan(params: dict[str, Any]):
    from traceability.plans import PlanError, load_plan, plans_dir

    plan_id = str(params.get("plan_id") or "")
    path = plans_dir() / f"{plan_id}.yaml"
    if "/" in plan_id or not path.is_file():
        raise ApiError(404, "Unknown plan", plan_id)
    try:
        return load_plan(path)
    except PlanError as exc:
        raise ApiError(422, "Invalid plan", str(exc)) from exc


@route("GET", "/plans")
def list_plans(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    from traceability.plans import load_plans

    return {
        "plans": [
            {"plan_id": p.id, "title": p.title, "release": p.release, "revision": p.revision}
            for p in load_plans()
        ]
    }


@route("GET", "/plans/{plan_id}")
def get_plan(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    plan = _plan(params)
    return {"plan_id": plan.id, "revision": plan.revision, "body": plan.body}


@route("GET", "/plans/{plan_id}/selection")
def get_plan_selection(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """The resolved test list, so an external runner can execute the same scope."""
    from pathlib import Path

    from traceability.plans import resolve_plan

    plan = _plan(params)
    return {"plan_id": plan.id, **resolve_plan(plan, Path.cwd()).to_json()}


@route("POST", "/plans/{plan_id}/evaluations")
def post_plan_evaluation(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    from traceability.evidence import Scope, StoreResultSource
    from traceability.plans import evaluate_plan
    from traceability.store import save_plan, save_plan_evaluation

    plan = _plan(params)
    run_ids = (body or {}).get("run_ids") if isinstance(body, dict) else None
    if not run_ids or not isinstance(run_ids, list):
        raise ApiError(400, "Invalid body", "run_ids must be a non-empty list")
    source = StoreResultSource(store, Scope(run_ids=tuple(map(str, run_ids))))
    evaluation = evaluate_plan(plan, source, run_ids, registry=_registry(store))
    save_plan(store, plan)
    save_plan_evaluation(store, evaluation)
    return evaluation.to_json()


@route("GET", "/plans/{plan_id}/evaluations/latest")
def get_latest_plan_evaluation(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    from traceability.store import latest_plan_evaluation

    plan = _plan(params)
    found = latest_plan_evaluation(store, plan.id)
    if found is None:
        raise ApiError(404, "No evaluation", f"plan {plan.id} has not been evaluated")
    return found


@route("GET", "/coverage-gaps/latest")
def get_latest_gaps(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    from traceability.store import latest_gap_report

    found = latest_gap_report(store, branch=params.get("branch") or None)
    if found is None:
        raise ApiError(404, "No gap report", "no coverage-gap report stored")
    return found


@route("GET", "/runs/{run_id}/coverage-gaps")
def get_run_gaps(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    from traceability.store import latest_gap_report

    found = latest_gap_report(store, run_id=str(params.get("run_id")))
    if found is None:
        raise ApiError(404, "No gap report", f"no coverage-gap report for run {params['run_id']}")
    return found


@route("GET", "/review/drafts")
def get_review_queue(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """Catalogue cases carrying the draft marker: the review queue (approval stays in PRs)."""
    import json

    rows = store.conn.execute(
        "SELECT case_id, case_key, file_path, markers_json FROM cases"
        " WHERE status = 'active' AND project_id = COALESCE(?, 'default') ORDER BY case_key",
        (params.get("project_id"),),
    ).fetchall()
    out = []
    for row in rows:
        markers = json.loads(row["markers_json"] or "[]")
        for mark in markers:
            name = mark.get("name") if isinstance(mark, dict) else mark
            if name == "draft":
                out.append(
                    {
                        "case_id": row["case_id"],
                        "case_key": row["case_key"],
                        "file_path": row["file_path"],
                    }
                )
                break
    return {"review_state": "draft", "cases": out}


@route("POST", "/releases/{release_id}/candidates")
def post_candidate(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """Register a candidate and the runs that tested it (body: candidate_id, run_ids, ...)."""
    from traceability.release import Candidate
    from traceability.store import load_candidate, save_candidate

    data = body if isinstance(body, dict) else {}
    cid = data.get("candidate_id")
    runs = data.get("run_ids")
    if not cid or not isinstance(runs, list) or not runs:
        raise ApiError(400, "Invalid body", "candidate_id and a non-empty run_ids are required")
    candidate = Candidate(
        str(params["release_id"]),
        str(cid),
        data.get("version"),
        data.get("commit"),
        data.get("plan_id"),
    )
    save_candidate(store, candidate, [str(r) for r in runs])
    return load_candidate(store, candidate.release_id, candidate.candidate_id) or {}


def _candidate(store: Any, params: dict[str, Any]) -> dict[str, Any]:
    from traceability.store import load_candidate

    found = load_candidate(store, str(params["release_id"]), str(params["candidate_id"]))
    if found is None:
        raise ApiError(404, "Unknown candidate", f"{params['release_id']}/{params['candidate_id']}")
    return found


@route("POST", "/releases/{release_id}/candidates/{candidate_id}/verdict")
def post_release_verdict(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """Compute and store the verdict from the candidate's runs in the store."""
    from traceability.evidence import Scope, StoreResultSource
    from traceability.release import (
        DEFAULT_POLICY,
        DEFAULT_WAIVERS,
        Candidate,
        decide_release,
        decision_json,
        load_policy,
        load_waivers,
    )
    from traceability.store import save_release_verdict

    found = _candidate(store, params)
    candidate = Candidate(
        found["release_id"],
        found["candidate_id"],
        found.get("version"),
        found.get("commit_sha"),
        found.get("plan_id"),
    )
    plan = None
    if candidate.plan_id:
        plan = _plan({"plan_id": candidate.plan_id})
    source = StoreResultSource(store, Scope(run_ids=tuple(found["run_ids"])))
    decision = decide_release(
        candidate,
        source,
        load_policy(DEFAULT_POLICY),
        registry=_registry(store),
        waivers=load_waivers(DEFAULT_WAIVERS),
        plan=plan,
    )
    doc = decision_json(decision)
    save_release_verdict(store, {**doc, "matrix": decision.matrix})
    return doc


@route("GET", "/releases/{release_id}/candidates/{candidate_id}/verdict")
def get_release_verdict(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    from traceability.store import latest_release_verdict

    _candidate(store, params)
    found = latest_release_verdict(store, str(params["release_id"]), str(params["candidate_id"]))
    if found is None:
        raise ApiError(404, "No verdict", "this candidate has no stored verdict")
    return found


@route("GET", "/defects")
def get_defects(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    from traceability.store import list_defects

    return {
        "defects": list_defects(
            store, state=params.get("state") or None, label=params.get("label") or None
        )
    }


@route("GET", "/defects/{defect_id}")
def get_defect(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    from traceability.store import defect_cases, list_defects

    defect_id = str(params["defect_id"])
    found = [d for d in list_defects(store) if d["defect_id"] == defect_id]
    cases = defect_cases(store, defect_id)
    if not found and not cases:
        raise ApiError(404, "Unknown defect", defect_id)
    return {"defect": found[0] if found else None, "cases": cases}


@route("POST", "/defects/drafts")
def post_defect_draft(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """Draft an issue for one stored failure (body: run_id, nodeid). Nothing is filed."""
    from traceability.defects import draft_from_failure

    data = body if isinstance(body, dict) else {}
    run_id, nodeid = data.get("run_id"), data.get("nodeid")
    if not run_id or not nodeid:
        raise ApiError(400, "Invalid body", "run_id and nodeid are required")
    rows = store.conn.execute(
        "SELECT outcome, message, longrepr FROM final_results WHERE run_id = ? AND nodeid = ?",
        (run_id, nodeid),
    ).fetchall()
    if not rows:
        raise ApiError(404, "Unknown result", f"{run_id} {nodeid}")
    row = dict(rows[0])
    if row["outcome"] not in ("failed", "error"):
        raise ApiError(409, "Not a failure", f"outcome is {row['outcome']}")
    draft = draft_from_failure(
        None, nodeid=nodeid, run_id=run_id, message=row.get("message") or row.get("longrepr") or ""
    )
    return draft.to_json()


@route("PUT", "/cases/{case_id}/defects/{defect_id}")
def put_case_defect(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """An external framework links one of its failures (a case) to a defect."""
    from verification.defect_detector import require_defect_id

    case_id = _case(store, params)
    try:
        defect_id = require_defect_id(str(params["defect_id"]))
    except ValueError as exc:
        raise ApiError(400, "Invalid defect id", str(exc)) from exc
    store.add_links([(case_id, "defect", defect_id)], source="import")
    return {"case_id": case_id, "defect_id": defect_id}


@route("GET", "/requirements")
def list_requirements(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    from traceability.store import load_registry

    out = []
    for req in load_registry(store):
        if params.get("status") and req.status != params["status"]:
            continue
        if params.get("release") and req.release != params["release"]:
            continue
        if params.get("label") and params["label"] not in req.labels:
            continue
        linked = store.conn.execute(
            "SELECT COUNT(DISTINCT case_id) FROM case_links WHERE kind = 'requirement' AND ref = ?",
            (req.id,),
        ).fetchone()[0]
        out.append({**req.to_json(), "linked_cases": linked})
    return {"requirements": out}


@route("GET", "/requirements/{requirement_id}")
def get_requirement(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    from traceability.store import load_registry

    req = load_registry(store).get(str(params["requirement_id"]))
    if req is None:
        raise ApiError(404, "Unknown requirement", str(params["requirement_id"]))
    return req.to_json()


@route("GET", "/requirements/syncs/latest")
def get_latest_sync(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    from traceability.store import latest_sync

    found = latest_sync(store)
    if found is None:
        raise ApiError(404, "No sync", "no requirements sync stored")
    return found


@route("GET", "/runs/{run_id}/summary")
def get_run_summary(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """The PR-summary rendering of one stored run, for any CI that posts its own comment."""
    from traceability.pr_comment import LaneSummary, SummaryInputs, render_pr_summary
    from traceability.store import latest_gap_report

    run_id = str(params["run_id"])
    run = store.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise ApiError(404, "Unknown run", run_id)
    counts = {
        r[0]: r[1]
        for r in store.conn.execute(
            "SELECT outcome, COUNT(*) FROM final_results WHERE run_id = ? GROUP BY outcome",
            (run_id,),
        )
    }
    verdicts = [
        dict(r)
        for r in store.conn.execute(
            "SELECT nodeid, kind, ticket, verdict, detail FROM verdicts WHERE run_id = ?",
            (run_id,),
        )
    ]
    lane = LaneSummary(
        run["lane"] or "default", run_id, run["integrity_verdict"] or "UNJUDGED", "", counts
    )
    inputs = SummaryInputs(
        lanes=[lane], verdicts=verdicts, gaps=latest_gap_report(store, run_id=run_id)
    )
    text = render_pr_summary(inputs)
    return {"run_id": run_id, "format": "md", "body": text}
