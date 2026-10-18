"""The run bundle: NDJSON, one typed record per line — the canonical interchange format.

Record types, in the order a bundle carries them: ``run``, ``invocation``, ``case``,
``revision``, ``result``, ``evidence``, ``verdict``, ``provenance``, ``run_end``. Every
line has ``type`` and ``schema_version: 1``; the shapes are the :mod:`results.models`
records and the contract is ``results/schemas/bundle-v1.schema.json``.

Any framework, in any language, pushes results by producing a bundle and handing it to
``python -m results import bundle FILE`` (or the REST handler); it never touches SQL.
Every run exports its bundle, so the database can always be rebuilt from bundles.
Importing a bundle twice changes nothing. A bundle ending in ``.gz`` is gzip-compressed.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import IO, Any

from results._util import loads
from results.models import (
    CaseRecord,
    EvidenceRecord,
    InvocationRecord,
    ProvenanceRecord,
    ResultRecord,
    RevisionRecord,
    RunRecord,
    VerdictRecord,
)

SCHEMA_VERSION = 1
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
BUNDLE_SCHEMA = "bundle-v1.schema.json"
RECORD_TYPES = (
    "run",
    "invocation",
    "case",
    "revision",
    "result",
    "evidence",
    "verdict",
    "provenance",
    "run_end",
)


class BundleError(ValueError):
    """A bundle line is unreadable or breaks the schema; ``line`` is 1-based."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line


@dataclass(frozen=True)
class BundleRecord:
    type: str
    data: dict[str, Any]
    line: int = 0

    def to_line(self) -> str:
        body = {"type": self.type, "schema_version": SCHEMA_VERSION, **self.data}
        return json.dumps(body, ensure_ascii=False, sort_keys=False, separators=(",", ":"))


@dataclass
class ImportReport:
    run_ids: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)  # records read per type
    added: dict[str, int] = field(default_factory=dict)  # rows new to the store per type
    rejected: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "run_ids": self.run_ids,
            "counts": self.counts,
            "added": self.added,
            "rejected": self.rejected,
        }


def load_schema(name: str = BUNDLE_SCHEMA) -> dict[str, Any]:
    path = SCHEMA_DIR / Path(name).name
    if not path.is_file():
        raise FileNotFoundError(f"no schema named {name!r}")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def _validator(name: str):
    from jsonschema import Draft202012Validator

    return Draft202012Validator(load_schema(name))


def validate_record(obj: Any, line: int = 0, schema: str = BUNDLE_SCHEMA) -> None:
    errors = sorted(_validator(schema).iter_errors(obj), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(record)"
        raise BundleError(line, f"{where}: {first.message}")


# ---------------------------------------------------------------------- reading


def _open_text(path: str | Path) -> IO[str]:
    p = Path(path)
    if p.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(p, "rb"), encoding="utf-8")
    return p.open("r", encoding="utf-8")


def parse_lines(lines: Iterable[str], *, validate: bool = True) -> Iterator[BundleRecord]:
    for number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except ValueError as exc:
            raise BundleError(number, f"not JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise BundleError(number, "a record must be a JSON object")
        if validate:
            validate_record(obj, number)
        kind = obj.get("type")
        if kind not in RECORD_TYPES:
            raise BundleError(number, f"unknown record type {kind!r}")
        data = {k: v for k, v in obj.items() if k not in ("type", "schema_version")}
        yield BundleRecord(kind, data, number)


def read_bundle(path: str | Path, *, validate: bool = True) -> Iterator[BundleRecord]:
    """Records of the bundle at ``path``; raises :class:`BundleError` with the line number."""
    with _open_text(path) as handle:
        yield from parse_lines(handle, validate=validate)


# ---------------------------------------------------------------------- writing


def _strip_ids(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in ("id", "invocation_id", "result_id")}


def bundle_records(store: Any, run_id: str) -> Iterator[BundleRecord]:
    """Every record of one run, in bundle order."""
    from results.store import StoreError, row_to_case, row_to_result, row_to_run

    runs = store.query("run_get", run_id=run_id)
    if not runs:
        raise StoreError(f"unknown run {run_id!r}")
    run = row_to_run(_strip_ids(runs[0]))
    head = run.to_json()
    end = {
        "run_id": run.run_id,
        "status": head.pop("status"),
        "finished_at": head.pop("finished_at"),
        "integrity_verdict": head.pop("integrity_verdict"),
        "project_id": run.project_id,
    }
    head["status"] = "running"
    yield BundleRecord("run", head)
    for row in store.query("run_invocations", run_id=run_id):
        row = _strip_ids(row)
        row["argv"] = tuple(loads(row.pop("argv_json", None), []) or ())
        yield BundleRecord("invocation", InvocationRecord.from_json(row).to_json())
    for row in store.query("run_cases", run_id=run_id):
        links = [
            (r["kind"], r["ref"])
            for r in store.query(
                "case_links_for", case_id=row["case_id"], project_id=row["project_id"]
            )
        ]
        yield BundleRecord("case", row_to_case(_strip_ids(row), links).to_json())
    for row in store.query("run_revisions", run_id=run_id):
        row = _strip_ids(row)
        row["steps"] = tuple(loads(row.pop("steps_json", None), []) or ())
        yield BundleRecord("revision", RevisionRecord.from_json(row).to_json())
    for row in store.query("run_results", run_id=run_id):
        yield BundleRecord("result", row_to_result(_strip_ids(row)).to_json())
    for row in store.query("run_evidence", run_id=run_id):
        row = _strip_ids(row)
        row["nodeid"] = row["nodeid"] or None
        row["attempt"] = row["attempt"] or None
        yield BundleRecord("evidence", EvidenceRecord.from_json(row).to_json())
    for row in store.query("run_verdicts", run_id=run_id):
        row = _strip_ids(row)
        for key in ("ticket", "subject", "recorded_at"):
            row[key] = row[key] or None
        yield BundleRecord("verdict", VerdictRecord.from_json(row).to_json())
    for row in store.query("run_provenance", run_id=run_id):
        row = _strip_ids(row)
        row["attributes"] = loads(row.pop("attributes_json", None), {}) or {}
        row["entity_id"] = row["entity_id"] or None
        row["recorded_at"] = row["recorded_at"] or None
        yield BundleRecord("provenance", ProvenanceRecord.from_json(row).to_json())
    yield BundleRecord("run_end", end)


def safe_name(run_id: str) -> str:
    """A run id as a file-name part (external ids may hold '/', spaces, ...)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip("-") or "run"


def bundle_path(out: Path, run_id: str) -> Path:
    out = Path(out)
    if out.suffix in (".ndjson", ".gz", ".jsonl"):
        return out
    return out / f"bundle-{safe_name(run_id)}.ndjson"


def write_bundle(store: Any, run_id: str, out: Path) -> Path:
    """Write the run's bundle to ``out`` (a file, or a directory for ``bundle-<run>.ndjson``)."""
    target = bundle_path(out, run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(r.to_line() + "\n" for r in bundle_records(store, run_id))
    if target.suffix == ".gz":
        # mtime=0 keeps the compressed bytes reproducible for identical content.
        with gzip.GzipFile(target, "wb", mtime=0) as handle:
            handle.write(text.encode("utf-8"))
    else:
        target.write_text(text, encoding="utf-8")
    return target


# ---------------------------------------------------------------------- importing


def import_records(store: Any, records: Iterable[BundleRecord]) -> ImportReport:
    """Apply already-parsed records in one transaction."""
    report = ImportReport()
    items = list(records)
    for rec in items:
        report.counts[rec.type] = report.counts.get(rec.type, 0) + 1

    def bump(kind: str, n: int) -> None:
        report.added[kind] = report.added.get(kind, 0) + n

    with store.transaction():
        for rec in items:
            if rec.type == "run":
                run = RunRecord.from_json(rec.data)
                existed = bool(store.query("run_get", run_id=run.run_id))
                store.import_run(run)
                bump("run", 0 if existed else 1)
                if run.run_id not in report.run_ids:
                    report.run_ids.append(run.run_id)
        by_type: dict[str, list[dict[str, Any]]] = {}
        for rec in items:
            by_type.setdefault(rec.type, []).append(rec.data)
        referenced = {
            d["run_id"] for t, ds in by_type.items() if t not in ("case", "revision") for d in ds
        }
        for run_id in sorted(referenced):
            if not store.query("run_get", run_id=run_id):
                raise BundleError(
                    0,
                    f"records reference run {run_id!r}, unknown to the bundle and the store",
                )

        def of(kind: str, model: Any) -> list[Any]:
            return [model.from_json(d) for d in by_type.get(kind, [])]

        invocations, results, evidence = _remap_merged_invocations(
            store,
            of("invocation", InvocationRecord),
            of("result", ResultRecord),
            of("evidence", EvidenceRecord),
        )
        bump("invocation", store.add_invocations(invocations))
        cases = of("case", CaseRecord)
        if cases:
            store.upsert_cases(cases)
            bump("case", len(cases))
        bump("revision", store.add_revisions(of("revision", RevisionRecord)))
        bump("result", store.add_results(results))
        bump("evidence", store.add_evidence(evidence))
        bump("verdict", store.add_verdicts(of("verdict", VerdictRecord)))
        bump("provenance", store.add_provenance(of("provenance", ProvenanceRecord)))
        for data in by_type.get("run_end", []):
            current = store.run(data["run_id"])
            if (
                current is not None
                and current.status == data["status"]
                and current.status != "running"
                and (data.get("finished_at") is None or data["finished_at"] == current.finished_at)
            ):
                continue  # already finished the same way: a re-import changes nothing
            store.finish_run(
                data["run_id"],
                data["status"],
                data.get("integrity_verdict"),
                finished_at=data.get("finished_at"),
            )
    _reindex_quietly(store)
    return report


def _reindex_quietly(store: Any) -> None:
    """Keep the search index current after an import; a failure here loses no results."""
    try:
        from results.search import reindex

        reindex(store)
    except Exception as exc:  # pragma: no cover - logged, never fatal
        import logging

        logging.getLogger(__name__).warning("search reindex after import failed: %s", exc)


def _remap_merged_invocations(
    store: Any,
    invocations: list[InvocationRecord],
    results: list[ResultRecord],
    evidence: list[EvidenceRecord],
) -> tuple[list[InvocationRecord], list[ResultRecord], list[EvidenceRecord]]:
    """Merge a bundle from *another* process of the same run without losing anything.

    Two CI jobs (one per lane) sharing one run id each export invocation 1. An invocation
    is identified by its start time: a bundle invocation the store already holds (under
    any sequence number) maps onto it, attempt for attempt, so a re-import adds nothing.
    A new one becomes the run's next invocation and its attempts continue after the
    attempts already stored for the same test.
    """
    seq_map: dict[tuple[str, int], int] = {}
    known: dict[tuple[str, int], dict[str, list[int]]] = {}  # stored attempts per test
    next_seq: dict[str, int] = {}
    out_invocations = []
    for inv in invocations:
        existing = store.invocation_seq_by_start(inv.run_id, inv.started_at, inv.argv)
        if existing is not None:
            seq_map[(inv.run_id, inv.seq)] = existing
            known[(inv.run_id, inv.seq)] = store.invocation_attempts(inv.run_id, existing)
            continue
        if store.invocation_started(inv.run_id, inv.seq) is False:
            out_invocations.append(inv)  # free sequence number: keep it as it is
            continue
        if inv.run_id not in next_seq:
            next_seq[inv.run_id] = store.next_invocation_seq(inv.run_id)
        seq_map[(inv.run_id, inv.seq)] = next_seq[inv.run_id]
        out_invocations.append(replace(inv, seq=next_seq[inv.run_id]))
        next_seq[inv.run_id] += 1
    if not seq_map:
        return out_invocations, results, evidence

    offsets: dict[str, dict[str, int]] = {}
    attempt_map: dict[tuple[str, str, int], int] = {}
    out_results = []
    ordered = sorted(results, key=lambda r: (r.run_id, r.nodeid, r.attempt))
    position: dict[tuple[str, int, str], int] = {}
    for res in ordered:
        key = (res.run_id, res.invocation) if res.invocation is not None else None
        if key not in seq_map:
            out_results.append(res)
            continue
        if key in known:
            stored = known[key].get(res.nodeid, [])
            idx = position.get((*key, res.nodeid), 0)
            position[(*key, res.nodeid)] = idx + 1
            if idx < len(stored):
                new_attempt = stored[idx]
                attempt_map[(res.run_id, res.nodeid, res.attempt)] = new_attempt
                out_results.append(replace(res, invocation=seq_map[key], attempt=new_attempt))
                continue
        if res.run_id not in offsets:
            offsets[res.run_id] = store.max_attempts(res.run_id)
        base = offsets[res.run_id].get(res.nodeid, 0)
        new_attempt = base + 1
        offsets[res.run_id][res.nodeid] = new_attempt
        attempt_map[(res.run_id, res.nodeid, res.attempt)] = new_attempt
        out_results.append(replace(res, invocation=seq_map[key], attempt=new_attempt))
    out_evidence = []
    for ev in evidence:
        mapped = attempt_map.get((ev.run_id, ev.nodeid or "", ev.attempt or 0))
        out_evidence.append(replace(ev, attempt=mapped) if mapped is not None else ev)
    return out_invocations, out_results, out_evidence


def import_bundle(store: Any, path: str | Path, *, validate: bool = True) -> ImportReport:
    """Validate the whole bundle first (nothing is written if any line is bad), then import."""
    return import_records(store, list(read_bundle(path, validate=validate)))


__all__ = [
    "BUNDLE_SCHEMA",
    "RECORD_TYPES",
    "SCHEMA_VERSION",
    "BundleError",
    "BundleRecord",
    "ImportReport",
    "bundle_records",
    "import_bundle",
    "import_records",
    "load_schema",
    "parse_lines",
    "read_bundle",
    "safe_name",
    "validate_record",
    "write_bundle",
]
