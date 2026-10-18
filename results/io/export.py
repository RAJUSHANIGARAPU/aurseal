"""CSV and JSON exports of cases and run results: pure formatters from store rows to bytes.

CSV is RFC 4180 (``\\r\\n`` line ends, minimal quoting, UTF-8 without BOM) with a fixed
column order; JSON validates against ``results/schemas/{cases,results}-export-v1``.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Literal

from results.catalogue import catalogue_from_store

CASE_COLUMNS = (
    "case_id",
    "case_key",
    "title",
    "owner",
    "priority",
    "status",
    "markers",
    "links",
    "steps",
    "file_path",
    "line",
    "current_revision_id",
)
RESULT_COLUMNS = (
    "run_id",
    "nodeid",
    "case_id",
    "outcome",
    "attempts",
    "duration_ms",
    "started_at",
    "finished_at",
    "worker",
    "browser",
    "message",
    "failure_fingerprint",
    "skip_reason",
    "defect_ref",
    "revision_id",
)

Format = Literal["csv", "json"]


def _csv(columns: tuple[str, ...], rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: "" if row.get(k) is None else row.get(k) for k in columns})
    return buffer.getvalue().encode("utf-8")


def case_rows(store: Any, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cat = catalogue_from_store(store, **(filters or {}))
    rows = []
    for c in cat.cases:
        rows.append(
            {
                "case_id": c.case_id,
                "case_key": c.case_key,
                "title": c.title,
                "owner": c.owner,
                "priority": c.priority,
                "status": c.status,
                "markers": [m.get("name", "") for m in c.markers],
                "links": [{"kind": k, "ref": r} for k, r in c.links],
                "steps": list(c.steps),
                "file_path": c.file_path,
                "line": c.line,
                "current_revision_id": c.current_revision_id,
            }
        )
    return rows


def export_cases(store: Any, fmt: Format, *, filters: dict[str, Any] | None = None) -> bytes:
    """The catalogue as CSV or JSON."""
    rows = case_rows(store, filters)
    if fmt == "json":
        body = {"schema_version": 1, "case_count": len(rows), "cases": rows}
        return json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8")
    flat = [
        {
            **r,
            "markers": " ".join(r["markers"]),
            "links": " ".join(f"{x['kind']}:{x['ref']}" for x in r["links"]),
            "steps": "\n".join(f"{i}. {s}" for i, s in enumerate(r["steps"], 1)),
        }
        for r in rows
    ]
    return _csv(CASE_COLUMNS, flat)


def result_rows(store: Any, run_id: str) -> list[dict[str, Any]]:
    rows = []
    for r in store.query("run_final_results", run_id=run_id):
        rows.append({k: r.get(k) for k in RESULT_COLUMNS})
    return rows


def export_results(store: Any, run_id: str, fmt: Format) -> bytes:
    """A run's final results as CSV or JSON (JSON carries the run header too)."""
    from results.store import StoreError, row_to_run

    runs = store.query("run_get", run_id=run_id)
    if not runs:
        raise StoreError(f"unknown run {run_id!r}")
    rows = result_rows(store, run_id)
    if fmt == "json":
        run = row_to_run(runs[0]).to_json()
        body = {"schema_version": 1, "run": run, "result_count": len(rows), "results": rows}
        return json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8")
    return _csv(RESULT_COLUMNS, rows)


__all__ = ["CASE_COLUMNS", "RESULT_COLUMNS", "export_cases", "export_results"]
