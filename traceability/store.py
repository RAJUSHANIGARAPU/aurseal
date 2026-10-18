"""Persist traceability records in the results store (``trace`` migration domain).

Files stay the primary evidence; the store is a cache of the same records for history
and the REST layer. Every function takes an open :class:`results.store.SqliteResultsStore`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from results.models import DEFAULT_PROJECT
from traceability._util import utc_now
from traceability.links import CaseLink
from traceability.requirements import Registry, Requirement


def save_registry(store: Any, registry: Registry, *, project_id: str = DEFAULT_PROJECT) -> int:
    """Make the stored registry equal ``registry``; returns how many rows were written.

    A requirement the registry no longer lists is marked retired, never left active.
    """
    now = utc_now()
    with store.transaction() as conn:
        ids = [r.id for r in registry]
        conn.execute(
            "UPDATE trace_requirements SET status = 'retired', synced_at = ?"
            f" WHERE project_id = ? AND requirement_id NOT IN ({','.join('?' * len(ids))})",
            (now, project_id, *ids),
        )
        for req in registry:
            conn.execute(
                "INSERT INTO trace_requirements (project_id, requirement_id, title, status,"
                " priority, source, source_ref, url, labels_json, release, parent_id,"
                " content_hash, synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (project_id, requirement_id) DO UPDATE SET title=excluded.title,"
                " status=excluded.status, priority=excluded.priority, source=excluded.source,"
                " source_ref=excluded.source_ref, url=excluded.url,"
                " labels_json=excluded.labels_json, release=excluded.release,"
                " parent_id=excluded.parent_id, content_hash=excluded.content_hash,"
                " synced_at=excluded.synced_at",
                (
                    project_id,
                    req.id,
                    req.title,
                    req.status,
                    req.priority,
                    req.source,
                    req.source_ref,
                    req.url,
                    json.dumps(list(req.labels)),
                    req.release,
                    req.parent_id,
                    req.content_hash,
                    now,
                ),
            )
    return len(registry)


def load_registry(store: Any, *, project_id: str = DEFAULT_PROJECT) -> Registry:
    rows = store.conn.execute(
        "SELECT * FROM trace_requirements WHERE project_id = ? ORDER BY requirement_id",
        (project_id,),
    ).fetchall()
    items = []
    for row in rows:
        data = dict(row)
        data["id"] = data.pop("requirement_id")
        data["labels"] = json.loads(data.pop("labels_json") or "[]")
        items.append(Requirement.from_json(data))
    return Registry.from_requirements(items)


def save_links(
    store: Any, links: Iterable[CaseLink], *, project_id: str = DEFAULT_PROJECT
) -> tuple[int, list[str]]:
    """Add requirement links for cases the store knows; returns (new links, unknown case ids).

    A link to a case the catalogue has never seen cannot be stored (``case_links`` keys on
    the case); it is returned instead of dropped silently.
    """
    known = {
        r[0]
        for r in store.conn.execute("SELECT case_id FROM cases WHERE project_id = ?", (project_id,))
    }
    rows: dict[str, list[tuple[str, str, str]]] = {}
    unknown: list[str] = []
    for link in links:
        case_id = link.case_id
        if not case_id or case_id not in known:
            unknown.append(case_id or link.nodeid)
            continue
        source = "marker" if link.origin == "marker" else "import"
        rows.setdefault(source, []).extend((case_id, "requirement", r) for r in link.requirements)
    added = sum(store.add_links(r, source=s, project_id=project_id) for s, r in rows.items())
    return added, sorted(set(unknown))


def case_requirements(
    store: Any, case_id: str, *, project_id: str = DEFAULT_PROJECT
) -> list[dict[str, str]]:
    rows = store.conn.execute(
        "SELECT ref, source FROM case_links WHERE project_id = ? AND case_id = ?"
        " AND kind = 'requirement' ORDER BY ref",
        (project_id, case_id),
    ).fetchall()
    return [{"requirement_id": r[0], "source": r[1]} for r in rows]


def replace_case_requirements(
    store: Any,
    case_id: str,
    requirement_ids: Iterable[str],
    *,
    project_id: str = DEFAULT_PROJECT,
) -> int:
    """Set the externally owned links of one case (marker links are left alone)."""
    with store.transaction() as conn:
        conn.execute(
            "DELETE FROM case_links WHERE project_id = ? AND case_id = ? AND kind = 'requirement'"
            " AND source = 'import'",
            (project_id, case_id),
        )
        return store.add_links(
            [(case_id, "requirement", r) for r in requirement_ids],
            source="import",
            project_id=project_id,
        )


def save_matrix(
    store: Any, matrix_doc: dict[str, Any], *, project_id: str = DEFAULT_PROJECT
) -> int:
    """Store one matrix JSON document; returns the snapshot id."""
    scope_key = json.dumps(matrix_doc.get("scope") or {}, sort_keys=True)
    with store.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO trace_matrix_snapshots (project_id, scope_key, generated_at,"
            " run_ids_json, body_json) VALUES (?, ?, ?, ?, ?)",
            (
                project_id,
                scope_key,
                matrix_doc.get("generated_at") or utc_now(),
                json.dumps(matrix_doc.get("run_ids") or []),
                json.dumps(matrix_doc, sort_keys=True),
            ),
        )
        return int(cur.lastrowid)


def load_matrix_snapshot(store: Any, snapshot_id: int) -> dict[str, Any] | None:
    row = store.conn.execute(
        "SELECT body_json FROM trace_matrix_snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()
    return json.loads(row[0]) if row else None


def save_plan(store: Any, plan: Any, *, project_id: str = DEFAULT_PROJECT) -> None:
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO trace_plans (project_id, plan_id, revision, release, body_json,"
            " stored_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (project_id, plan.id, plan.revision, plan.release, json.dumps(plan.body), utc_now()),
        )


def save_plan_evaluation(store: Any, evaluation: Any, *, project_id: str = DEFAULT_PROJECT) -> int:
    doc = evaluation.to_json()
    with store.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO trace_plan_evaluations (project_id, plan_id, plan_revision,"
            " evaluated_at, run_ids_json, result, criteria_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                doc["plan_id"],
                doc["plan_revision"],
                doc["evaluated_at"],
                json.dumps(doc["run_ids"]),
                doc["result"],
                json.dumps(doc["criteria"]),
            ),
        )
        return int(cur.lastrowid)


def latest_plan_evaluation(
    store: Any, plan_id: str, *, project_id: str = DEFAULT_PROJECT
) -> dict[str, Any] | None:
    row = store.conn.execute(
        "SELECT * FROM trace_plan_evaluations WHERE project_id = ? AND plan_id = ?"
        " ORDER BY id DESC LIMIT 1",
        (project_id, plan_id),
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["run_ids"] = json.loads(data.pop("run_ids_json"))
    data["criteria"] = json.loads(data.pop("criteria_json"))
    return data


def save_gap_report(
    store: Any,
    doc: dict[str, Any],
    *,
    run_id: str | None = None,
    branch: str | None = None,
    baseline_hash: str | None = None,
    project_id: str = DEFAULT_PROJECT,
) -> int:
    with store.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO trace_gap_reports (project_id, run_id, branch, generated_at, mode,"
            " sources_json, gap_count, new_gap_count, baseline_hash, body_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                run_id,
                branch,
                doc["generated_at"],
                doc["mode"],
                json.dumps(doc["sources"]),
                doc["counts"].get("gap", 0),
                len(doc.get("new_gaps") or ()),
                baseline_hash,
                json.dumps(doc, sort_keys=True),
            ),
        )
        return int(cur.lastrowid)


def latest_gap_report(
    store: Any,
    *,
    run_id: str | None = None,
    branch: str | None = None,
    project_id: str = DEFAULT_PROJECT,
) -> dict[str, Any] | None:
    sql = "SELECT body_json, run_id, branch FROM trace_gap_reports WHERE project_id = ?"
    params: list[Any] = [project_id]
    if run_id is not None:
        sql += " AND run_id = ?"
        params.append(run_id)
    if branch is not None:
        sql += " AND branch = ?"
        params.append(branch)
    row = store.conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
    if row is None:
        return None
    doc = json.loads(row[0])
    doc.update(run_id=row[1], branch=row[2])
    return doc


def save_candidate(
    store: Any,
    candidate: Any,
    run_ids: list[str],
    *,
    plan_revision: str | None = None,
    project_id: str = DEFAULT_PROJECT,
) -> None:
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO trace_release_candidates (project_id, release_id, candidate_id, version,"
            " commit_sha, plan_id, plan_revision, run_ids_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (project_id, release_id, candidate_id) DO UPDATE SET"
            " version = excluded.version, commit_sha = excluded.commit_sha,"
            " plan_id = excluded.plan_id, plan_revision = excluded.plan_revision,"
            " run_ids_json = excluded.run_ids_json",
            (
                project_id,
                candidate.release_id,
                candidate.candidate_id,
                candidate.version,
                candidate.commit,
                candidate.plan_id,
                plan_revision,
                json.dumps(run_ids),
                utc_now(),
            ),
        )


def load_candidate(
    store: Any, release_id: str, candidate_id: str, *, project_id: str = DEFAULT_PROJECT
) -> dict[str, Any] | None:
    row = store.conn.execute(
        "SELECT * FROM trace_release_candidates WHERE project_id = ? AND release_id = ?"
        " AND candidate_id = ?",
        (project_id, release_id, candidate_id),
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["run_ids"] = json.loads(data.pop("run_ids_json"))
    return data


def save_release_verdict(
    store: Any, doc: dict[str, Any], *, project_id: str = DEFAULT_PROJECT
) -> int:
    snapshot = None
    if doc.get("matrix"):
        snapshot = save_matrix(store, doc["matrix"], project_id=project_id)
    body = {k: v for k, v in doc.items() if k != "matrix"}
    with store.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO trace_release_verdicts (project_id, release_id, candidate_id,"
            " computed_at, verdict, inputs_hash, policy_revision, reasons_json,"
            " matrix_snapshot_id, body_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                doc["candidate"]["release_id"],
                doc["candidate"]["candidate_id"],
                doc["computed_at"],
                doc["verdict"],
                doc["inputs_hash"],
                doc.get("policy_revision"),
                json.dumps(doc["reasons"]),
                snapshot,
                json.dumps(body, sort_keys=True),
            ),
        )
        return int(cur.lastrowid)


def latest_release_verdict(
    store: Any, release_id: str, candidate_id: str, *, project_id: str = DEFAULT_PROJECT
) -> dict[str, Any] | None:
    row = store.conn.execute(
        "SELECT body_json, matrix_snapshot_id FROM trace_release_verdicts WHERE project_id = ?"
        " AND release_id = ? AND candidate_id = ? ORDER BY id DESC LIMIT 1",
        (project_id, release_id, candidate_id),
    ).fetchone()
    if row is None:
        return None
    doc = json.loads(row[0])
    doc["matrix_snapshot_id"] = row[1]
    return doc


def save_defects(store: Any, snapshot: Any, *, project_id: str = DEFAULT_PROJECT) -> int:
    """Upsert every defect state of a :class:`traceability.defects.Snapshot`."""
    now = snapshot.generated_at or utc_now()
    with store.transaction() as conn:
        for d in snapshot.defects.values():
            conn.execute(
                "INSERT INTO trace_defects (project_id, defect_id, tracker, title, state,"
                " severity, labels_json, url, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (project_id, defect_id) DO UPDATE SET tracker = excluded.tracker,"
                " title = excluded.title, state = excluded.state, severity = excluded.severity,"
                " labels_json = excluded.labels_json, url = excluded.url,"
                " fetched_at = excluded.fetched_at",
                (
                    project_id,
                    d.id,
                    d.tracker,
                    d.title,
                    d.state,
                    d.severity,
                    json.dumps(list(d.labels)),
                    d.url,
                    now,
                ),
            )
    return len(snapshot.defects)


def list_defects(
    store: Any,
    *,
    state: str | None = None,
    label: str | None = None,
    project_id: str = DEFAULT_PROJECT,
) -> list[dict[str, Any]]:
    rows = store.conn.execute(
        "SELECT * FROM trace_defects WHERE project_id = ? AND (? IS NULL OR state = ?)"
        " ORDER BY defect_id",
        (project_id, state, state),
    ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["labels"] = json.loads(data.pop("labels_json") or "[]")
        if label is None or label in data["labels"]:
            out.append(data)
    return out


def defect_cases(store: Any, defect_id: str, *, project_id: str = DEFAULT_PROJECT) -> list[str]:
    rows = store.conn.execute(
        "SELECT case_id FROM case_links WHERE project_id = ? AND kind = 'defect' AND ref = ?"
        " ORDER BY case_id",
        (project_id, defect_id),
    )
    return [r[0] for r in rows]


def save_sync(
    store: Any, report: Any, *, error: str | None = None, project_id: str = DEFAULT_PROJECT
) -> int:
    doc = report.to_json()
    with store.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO trace_requirement_syncs (project_id, sources_json, started_at,"
            " finished_at, added, changed, retired, orphaned_links_json, error, body_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                json.dumps(doc["sources"]),
                doc["started_at"],
                doc["finished_at"] or None,
                len(doc["added"]),
                len(doc["changed"]) + len(doc["retitled"]),
                len(doc["retired"]) + len(doc["vanished"]),
                json.dumps(doc["orphaned_links"]),
                error,
                json.dumps(doc, sort_keys=True),
            ),
        )
        return int(cur.lastrowid)


def latest_sync(store: Any, *, project_id: str = DEFAULT_PROJECT) -> dict[str, Any] | None:
    row = store.conn.execute(
        "SELECT body_json, error FROM trace_requirement_syncs WHERE project_id = ?"
        " ORDER BY id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    doc = json.loads(row[0])
    doc["error"] = row[1]
    return doc
