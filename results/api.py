"""Framework-agnostic REST handlers over the results store.

Each handler is a pure function ``handler(store, params, body) -> dict``: ``params``
holds the path and query parameters (strings), ``body`` the decoded request body (a dict,
a list, or NDJSON text for bundle pushes). Errors raise :class:`ApiError` carrying an
HTTP status, which the hosting web layer turns into a problem response. :data:`ROUTES`
lists ``(method, path template, handler)`` for mounting under ``/api/v1``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from results.bundle import (
    BundleError,
    bundle_records,
    import_records,
    load_schema,
    parse_lines,
)
from results.models import INTEGRITY_VERDICTS, OUTCOMES, RUN_STATUSES, RunRecord
from results.store import StoreError, row_to_run

Handler = Callable[[Any, dict[str, Any], Any], Any]


class ApiError(Exception):
    def __init__(self, status: int, title: str, detail: str = "") -> None:
        super().__init__(f"{status} {title}: {detail}")
        self.status = status
        self.title = title
        self.detail = detail

    def to_problem(self) -> dict[str, Any]:
        return {"title": self.title, "status": self.status, "detail": self.detail}


def _int(params: dict[str, Any], name: str, default: int, *, low: int = 0, high: int = 1000) -> int:
    raw = params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "Bad parameter", f"{name} must be an integer") from exc
    return max(low, min(high, value))


def _require_run(store: Any, run_id: str) -> dict[str, Any]:
    rows = store.query("run_get", run_id=run_id)
    if not rows:
        raise ApiError(404, "Unknown run", f"no run {run_id!r}")
    return rows[0]


def create_run(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """POST /runs — open a run; the id is client-supplied or generated."""
    data = dict(body or {})
    data.setdefault("run_id", f"api-{uuid.uuid4().hex[:16]}")
    data["status"] = "running"
    data.setdefault("source", "api")
    try:
        run = RunRecord.from_json(data)
        store.begin_run(run)
    except (TypeError, StoreError) as exc:
        raise ApiError(400, "Invalid run", str(exc)) from exc
    except Exception as exc:  # sqlite CHECK violations and the like
        raise ApiError(400, "Invalid run", str(exc)) from exc
    return {"run_id": run.run_id}


def push_bundle(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """POST /runs/{run_id}/bundle — push NDJSON bundle records. Idempotent."""
    run_id = params.get("run_id")
    if isinstance(body, str):
        lines = body.splitlines()
    else:
        lines = [json.dumps(item) for item in (body or [])]
    try:
        records = list(parse_lines(lines))
    except BundleError as exc:
        raise ApiError(400, "Bundle rejected", str(exc)) from exc
    if run_id is not None:
        for rec in records:
            other = rec.data.get("run_id")
            if other is not None and other != run_id:
                raise ApiError(
                    400, "Bundle rejected", f"line {rec.line}: run_id {other!r} != {run_id!r}"
                )
    try:
        return import_records(store, records).to_json()
    except (BundleError, StoreError) as exc:
        raise ApiError(409, "Bundle not applicable", str(exc)) from exc


def import_bundle_body(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """POST /imports/bundle — the bundle push for a run that is not open yet."""
    return push_bundle(store, {}, body)


def finish_run(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """PATCH /runs/{run_id} — finish a run (status, integrity verdict)."""
    run_id = params["run_id"]
    _require_run(store, run_id)
    data = dict(body or {})
    status = data.get("status", "finished")
    verdict = data.get("integrity_verdict")
    if status not in RUN_STATUSES:
        raise ApiError(400, "Bad status", f"status must be one of {', '.join(RUN_STATUSES)}")
    if verdict is not None and verdict not in INTEGRITY_VERDICTS:
        raise ApiError(400, "Bad verdict", f"integrity_verdict must be one of {INTEGRITY_VERDICTS}")
    store.finish_run(run_id, status, verdict, finished_at=data.get("finished_at"))
    return row_to_run(_require_run(store, run_id)).to_json()


def list_runs(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /runs?lane=&branch=&status=&since=&limit=&offset="""
    rows = store.query(
        "runs_list",
        lane=params.get("lane"),
        branch=params.get("branch"),
        status=params.get("status"),
        since=params.get("since"),
        project_id=params.get("project"),
        limit=_int(params, "limit", 100, low=1),
        offset=_int(params, "offset", 0, high=10**9),
    )
    return {"runs": [{**row_to_run(r).to_json(), "result_count": r["result_count"]} for r in rows]}


def get_run(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /runs/{run_id} — the run plus its final-outcome tally."""
    run = row_to_run(_require_run(store, params["run_id"])).to_json()
    tally = {o: 0 for o in OUTCOMES}
    for row in store.query("run_final_results", run_id=params["run_id"]):
        tally[row["outcome"]] += 1
    return {**run, "tally": tally}


def run_results(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /runs/{run_id}/results?outcome=&attempts=all — final attempts by default."""
    from results.store import row_to_result

    _require_run(store, params["run_id"])
    outcome = params.get("outcome")
    if outcome is not None and outcome not in OUTCOMES:
        raise ApiError(400, "Bad outcome", f"outcome must be one of {', '.join(OUTCOMES)}")
    name = "run_results" if params.get("attempts") == "all" else "run_final_results"
    rows = store.query(name, run_id=params["run_id"], outcome=outcome)
    return {"results": [row_to_result(r).to_json() for r in rows]}


def get_bundle(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /runs/{run_id}/bundle — the run's bundle as NDJSON text."""
    _require_run(store, params["run_id"])
    text = "".join(r.to_line() + "\n" for r in bundle_records(store, params["run_id"]))
    return {"content_type": "application/x-ndjson", "body": text}


def get_schema(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /schemas/{name} — the JSON Schemas, so external pushers can validate first."""
    name = str(params.get("name", ""))
    if not name.endswith(".json"):
        name += ".schema.json" if not name.endswith(".schema") else ".json"
    try:
        return load_schema(name)
    except FileNotFoundError as exc:
        raise ApiError(404, "Unknown schema", name) from exc


def list_cases(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /cases?marker=&owner=&priority=&status=&requirement=&feature="""
    from results.catalogue import catalogue_from_store

    filters = {
        k: params.get(k)
        for k in ("marker", "owner", "priority", "status", "requirement", "feature")
        if params.get(k)
    }
    cat = catalogue_from_store(store, project_id=params.get("project") or "default", **filters)
    return {"cases": [c.to_json() for c in cat.cases]}


def get_case(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /cases/{case_id} — one case, with links and current revision."""
    from results.store import row_to_case

    project = params.get("project") or "default"
    rows = store.query("case_get", case_id=params["case_id"], project_id=project)
    if not rows:
        raise ApiError(404, "Unknown case", params["case_id"])
    links = [
        (r["kind"], r["ref"])
        for r in store.query("case_links_for", case_id=params["case_id"], project_id=project)
    ]
    return row_to_case(rows[0], links).to_json()


def get_catalogue(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /catalogue — the full stored catalogue as catalogue JSON."""
    from results.catalogue import catalogue_from_store

    return catalogue_from_store(store, project_id=params.get("project") or "default").to_json()


def case_revisions(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /cases/{case_id}/revisions — revisions, each with the run range it covered."""
    from results.revision import revision_ranges

    project = params.get("project") or "default"
    if not store.query("case_get", case_id=params["case_id"], project_id=project):
        raise ApiError(404, "Unknown case", params["case_id"])
    return {"revisions": revision_ranges(store, params["case_id"], project_id=project)}


def _window(params: dict[str, Any], default: int = 30) -> int:
    return _int(params, "window", default, low=1, high=10_000)


def _flag(params: dict[str, Any], name: str) -> bool:
    return str(params.get(name, "")).lower() in ("1", "true", "yes")


def case_history_handler(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /cases/{case_id}/history?limit= — outcome history for one case."""
    from dataclasses import asdict

    from results.trends import case_history

    points = case_history(store, params["case_id"], limit=_int(params, "limit", 50, low=1))
    return {"case_id": params["case_id"], "history": [asdict(p) for p in points]}


def case_trend_handler(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /cases/{case_id}/trend?window=&include_unsound= — computed trend for one case."""
    from results.trends import case_trend

    trend = case_trend(
        store,
        params["case_id"],
        window=_window(params),
        include_unsound=_flag(params, "include_unsound"),
    )
    return trend.to_json()


def suite_trend_handler(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /trends/suite?lane=&branch=&window= — per-run suite trend."""
    from results.trends import suite_trend

    points = suite_trend(
        store, lane=params.get("lane"), branch=params.get("branch"), window=_window(params)
    )
    return {"runs": [p.to_json() for p in points]}


def compare_handler(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /runs/{run_id}/compare?baseline= — run compare."""
    from results.trends import compare_runs

    _require_run(store, params["run_id"])
    if params.get("baseline"):
        _require_run(store, params["baseline"])
    return compare_runs(store, params["run_id"], params.get("baseline")).to_json()


def unstable_handler(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /trends/unstable?window=&min_flips=&lane="""
    from results.trends import unstable

    return {
        "cases": unstable(
            store,
            window=_window(params),
            min_flips=_int(params, "min_flips", 2, low=1),
            lane=params.get("lane"),
        )
    }


def slowest_handler(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /trends/slowest?window=&top=&lane="""
    from results.trends import slowest

    return {
        "cases": slowest(
            store,
            window=_window(params),
            top=_int(params, "top", 20, low=1),
            lane=params.get("lane"),
        )
    }


def search_handler(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /search?q=&limit=&offset= — hits with doc_type, doc_ref, snippet, score, engine."""
    from results.search import search

    return search(
        store,
        str(params.get("q") or ""),
        limit=_int(params, "limit", 50, low=1),
        offset=_int(params, "offset", 0, high=10**9),
    ).to_json()


def _download(content_type: str, data: bytes) -> dict[str, Any]:
    return {"content_type": content_type, "body": data.decode("utf-8")}


def import_junit_handler(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """POST /imports/junit?framework=&run_id= — body is the JUnit XML."""
    from results.io.junit import JUnitError, import_junit

    data = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
    try:
        report = import_junit(
            store, data, framework=params.get("framework") or "junit", run_id=params.get("run_id")
        )
    except (JUnitError, ValueError) as exc:
        raise ApiError(400, "JUnit rejected", str(exc)) from exc
    return report.to_json()


def export_cases_handler(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /exports/cases.csv | /exports/cases.json"""
    from results.io.export import export_cases

    fmt = params["file"].rsplit(".", 1)[-1]
    if params["file"] not in ("cases.csv", "cases.json"):
        raise ApiError(404, "Not found", params["file"])
    data = export_cases(store, fmt)  # type: ignore[arg-type]
    return _download("text/csv" if fmt == "csv" else "application/json", data)


def export_results_handler(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /exports/runs/{run_id}/results.csv | results.json | testrail | xray"""
    from results.io.adapters.base import mapping_from_store
    from results.io.adapters.testrail import TestRailAdapter
    from results.io.adapters.xray import XrayAdapter
    from results.io.export import export_results, result_rows

    run_row = _require_run(store, params["run_id"])
    name = params["file"]
    if name in ("results.csv", "results.json"):
        fmt = name.rsplit(".", 1)[-1]
        data = export_results(store, params["run_id"], fmt)  # type: ignore[arg-type]
        return _download("text/csv" if fmt == "csv" else "application/json", data)
    adapters = {"testrail": TestRailAdapter, "xray": XrayAdapter}
    if name not in adapters:
        raise ApiError(404, "Not found", name)
    adapter = adapters[name]()
    run = row_to_run(run_row)
    rows = result_rows(store, params["run_id"])
    mapped = adapter.map_results(run, rows, mapping_from_store(store, adapter.link_kind))
    return {"payload": mapped.payload, "unmapped": mapped.unmapped, "omitted": mapped.omitted}


def docs_list(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /docs — living pages with their status."""
    from pathlib import Path

    from results.livedocs import DEFAULT_DOCS_DIR, page_summaries

    docs_dir = Path(params.get("docs_dir") or DEFAULT_DOCS_DIR)
    return {"pages": page_summaries(store, docs_dir)}


def docs_page(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /docs/{page} — the rendered page (Markdown) plus its status."""
    from pathlib import Path

    from results.livedocs import DEFAULT_DOCS_DIR, page_status, parse_page, render_page

    docs_dir = Path(params.get("docs_dir") or DEFAULT_DOCS_DIR)
    name = Path(str(params["page"])).name
    path = docs_dir / (name if name.endswith(".md") else f"{name}.md")
    if not path.is_file():
        raise ApiError(404, "Unknown page", name)
    page = parse_page(path)
    return {
        "page": page.name,
        "title": page.title,
        "status": page_status(store, page).to_json(),
        "markdown": render_page(store, page, fmt="md"),
        "issues": [str(i) for i in page.issues],
    }


def feature_cases(store: Any, params: dict[str, Any], body: Any) -> dict[str, Any]:
    """GET /features/{feature}/cases — cases linked to one feature."""
    from results.livedocs import linked_cases

    return {"cases": [c.to_json() for c in linked_cases(store, feature=params["feature"])]}


ROUTES: list[tuple[str, str, Handler]] = [
    ("POST", "/runs", create_run),
    ("POST", "/runs/{run_id}/bundle", push_bundle),
    ("POST", "/imports/bundle", import_bundle_body),
    ("PATCH", "/runs/{run_id}", finish_run),
    ("GET", "/runs", list_runs),
    ("GET", "/runs/{run_id}", get_run),
    ("GET", "/runs/{run_id}/results", run_results),
    ("GET", "/runs/{run_id}/bundle", get_bundle),
    ("GET", "/schemas/{name}", get_schema),
    ("GET", "/cases", list_cases),
    ("GET", "/cases/{case_id}", get_case),
    ("GET", "/catalogue", get_catalogue),
    ("GET", "/cases/{case_id}/revisions", case_revisions),
    ("GET", "/cases/{case_id}/history", case_history_handler),
    ("GET", "/cases/{case_id}/trend", case_trend_handler),
    ("GET", "/trends/suite", suite_trend_handler),
    ("GET", "/trends/unstable", unstable_handler),
    ("GET", "/trends/slowest", slowest_handler),
    ("GET", "/runs/{run_id}/compare", compare_handler),
    ("GET", "/search", search_handler),
    ("POST", "/imports/junit", import_junit_handler),
    ("GET", "/exports/{file}", export_cases_handler),
    ("GET", "/exports/runs/{run_id}/{file}", export_results_handler),
    ("GET", "/docs", docs_list),
    ("GET", "/docs/{page}", docs_page),
    ("GET", "/features/{feature}/cases", feature_cases),
]


def add_routes(routes: list[tuple[str, str, Handler]]) -> None:
    """Later features append their handlers here, so one table describes the whole API."""
    for route in routes:
        if route not in ROUTES:
            ROUTES.append(route)


def dispatch(
    store: Any, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None
) -> Any:
    """Match ``path`` (relative to ``/api/v1``) against :data:`ROUTES` and call the handler."""
    parts = [p for p in path.strip("/").split("/") if p]
    for verb, template, handler in ROUTES:
        if verb != method.upper():
            continue
        tparts = [p for p in template.strip("/").split("/") if p]
        if len(tparts) != len(parts):
            continue
        bound: dict[str, Any] = {}
        for t, p in zip(tparts, parts, strict=True):
            if t.startswith("{") and t.endswith("}"):
                bound[t[1:-1]] = p
            elif t != p:
                break
        else:
            return handler(store, {**(params or {}), **bound}, body)
    raise ApiError(404, "Not found", f"{method} {path}")


__all__ = ["ROUTES", "ApiError", "add_routes", "dispatch"]
