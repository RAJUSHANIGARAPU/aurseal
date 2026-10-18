"""The results store: a narrow ``ResultsStore`` protocol and its SQLite implementation.

Single-writer rule: inside pytest only the controller process opens the database (see
:mod:`results.pytest_plugin`); workers hand everything to it through their reports. A
non-pytest writer goes through the REST handlers in :mod:`results.api` or imports run
bundles one at a time.

Reads go through *named* queries (``results/queries/<name>.sql``); there is no ad-hoc SQL
in the public interface, so the dashboard, the REST handlers and the CLI share one
implementation of every question. Extension domains add their own query directories with
:func:`register_queries`.

SQLite on a network filesystem is unsafe (its locking is not reliable there): keep
``RESULTS_DB`` on a local disk.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from results import migrations
from results._util import (
    LONGREPR_LIMIT,
    MESSAGE_LIMIT,
    default_db_path,
    dumps,
    loads,
    truncate,
    utc_now,
)
from results.models import (
    DEFAULT_PROJECT,
    CaseRecord,
    EvidenceRecord,
    InvocationRecord,
    ProvenanceRecord,
    ResultRecord,
    RevisionRecord,
    RunRecord,
    VerdictRecord,
)

QUERIES_DIR = Path(__file__).resolve().parent / "queries"
OPEN_LOG_ENV = "RESULTS_OPEN_LOG"  # test hook: append "<pid>\n" whenever a store opens
_QUERY_NAME = re.compile(r"^[a-z0-9_]+$")
_PARAM = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")

_query_dirs: list[Path] = [QUERIES_DIR]


class StoreError(RuntimeError):
    """The store cannot do what was asked (unknown run, unknown query, bad record)."""


def register_queries(directory: Path) -> None:
    """Add a directory of named ``*.sql`` queries (extension domains)."""
    path = Path(directory)
    if path not in _query_dirs:
        _query_dirs.append(path)


def _query_sql(name: str) -> str:
    if not _QUERY_NAME.match(name):
        raise StoreError(f"invalid query name {name!r}")
    for directory in _query_dirs:
        path = directory / f"{name}.sql"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise StoreError(f"unknown query {name!r}")


def query_names() -> list[str]:
    names: set[str] = set()
    for directory in _query_dirs:
        names.update(p.stem for p in directory.glob("*.sql"))
    return sorted(names)


@runtime_checkable
class ResultsStore(Protocol):
    """What callers may rely on, whatever the engine underneath."""

    def begin_run(self, run: RunRecord) -> None: ...

    def begin_invocation(self, run_id: str, argv: Sequence[str], workers: int | None) -> int: ...

    def upsert_cases(self, cases: Iterable[CaseRecord]) -> None: ...

    def add_results(self, rows: Iterable[ResultRecord]) -> int: ...

    def add_evidence(self, rows: Iterable[EvidenceRecord]) -> int: ...

    def ingest_verdicts(self, path: str | Path | None, run_id: str) -> int: ...

    def finish_run(
        self, run_id: str, status: str, integrity_verdict: str | None = None
    ) -> None: ...

    def query(self, sql_name: str, **params: Any) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


def _log_open() -> None:
    target = os.environ.get(OPEN_LOG_ENV, "").strip()
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")
    except OSError:
        pass


def _blank(value: str | None) -> str:
    return value if value else ""


def _none(value: str | None) -> str | None:
    return value if value else None


class SqliteResultsStore:
    """``ResultsStore`` on one SQLite file (WAL, busy_timeout 30 s, foreign keys on)."""

    def __init__(self, path: str | Path, *, readonly: bool = False) -> None:
        self.path = Path(path)
        self.readonly = readonly
        _log_open()
        if readonly:
            if not self.path.is_file():
                raise StoreError(f"no results database at {self.path}")
            uri = f"file:{self.path.resolve()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
        else:
            if str(self.path) != ":memory:":
                self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        self.conn = conn
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA foreign_keys = ON")
            if readonly:
                migrations.verify(conn)
            else:
                if str(self.path) != ":memory:":
                    conn.execute("PRAGMA journal_mode = WAL")
                migrations.apply(conn)
        except Exception:
            conn.close()
            raise

    # ------------------------------------------------------------------ plumbing

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One write transaction (nested calls join the outer one).

        For modules inside this repository that own an extension domain; other callers
        use the protocol methods.
        """
        if self.conn.in_transaction:
            yield self.conn
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> SqliteResultsStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _run_project(self, run_id: str) -> str:
        row = self.conn.execute(
            "SELECT project_id FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"unknown run {run_id!r}: begin_run first")
        return row[0]

    def ensure_project(self, project_id: str, name: str = "") -> None:
        self.conn.execute(
            "INSERT INTO projects (id, project_key, name) VALUES (?, ?, ?)"
            " ON CONFLICT (id) DO NOTHING",
            (project_id, project_id, name or project_id),
        )

    # ------------------------------------------------------------------ runs

    def begin_run(self, run: RunRecord) -> None:
        """Create the run, or reopen it for another invocation (same run id)."""
        with self.transaction() as conn:
            self.ensure_project(run.project_id)
            conn.execute(
                """
                INSERT INTO runs (project_id, run_id, external_id, name, source, framework,
                    lane, environment, sut_version, git_sha, git_branch, ci_provider, ci_url,
                    trigger, schedule_name, run_request_id, release_label, expected_count, host,
                    started_at, finished_at, status, integrity_verdict, provenance_json,
                    metadata_json)
                VALUES (:project_id, :run_id, :external_id, :name, :source, :framework, :lane,
                    :environment, :sut_version, :git_sha, :git_branch, :ci_provider, :ci_url,
                    :trigger, :schedule_name, :run_request_id, :release_label, :expected_count,
                    :host, :started_at, :finished_at, :status, :integrity_verdict,
                    :provenance_json, :metadata_json)
                ON CONFLICT (run_id) DO UPDATE SET
                    status = CASE WHEN excluded.status = 'running' THEN 'running'
                                  ELSE runs.status END,
                    finished_at = CASE WHEN excluded.status = 'running' THEN NULL
                                       ELSE runs.finished_at END,
                    external_id = COALESCE(runs.external_id, excluded.external_id),
                    name = COALESCE(runs.name, excluded.name),
                    framework = COALESCE(runs.framework, excluded.framework),
                    lane = COALESCE(runs.lane, excluded.lane),
                    environment = COALESCE(runs.environment, excluded.environment),
                    sut_version = COALESCE(excluded.sut_version, runs.sut_version),
                    git_sha = COALESCE(runs.git_sha, excluded.git_sha),
                    git_branch = COALESCE(runs.git_branch, excluded.git_branch),
                    ci_provider = COALESCE(runs.ci_provider, excluded.ci_provider),
                    ci_url = COALESCE(runs.ci_url, excluded.ci_url),
                    trigger = COALESCE(runs.trigger, excluded.trigger),
                    schedule_name = COALESCE(runs.schedule_name, excluded.schedule_name),
                    run_request_id = COALESCE(runs.run_request_id, excluded.run_request_id),
                    release_label = COALESCE(excluded.release_label, runs.release_label),
                    expected_count = COALESCE(excluded.expected_count, runs.expected_count),
                    host = COALESCE(runs.host, excluded.host),
                    started_at = COALESCE(runs.started_at, excluded.started_at),
                    provenance_json = COALESCE(excluded.provenance_json, runs.provenance_json),
                    metadata_json = COALESCE(excluded.metadata_json, runs.metadata_json)
                """,
                self._run_params(run),
            )

    @staticmethod
    def _run_params(run: RunRecord) -> dict[str, Any]:
        data = run.to_json()
        data["provenance_json"] = dumps(data.pop("provenance"))
        data["metadata_json"] = dumps(data.pop("metadata"))
        data["started_at"] = data["started_at"] or utc_now()
        return data

    def import_run(self, run: RunRecord) -> None:
        """Insert a run exactly as given (bundle import); an existing run is left alone."""
        with self.transaction() as conn:
            self.ensure_project(run.project_id)
            params = self._run_params(run)
            cols = ", ".join(params)
            conn.execute(
                f"INSERT INTO runs ({cols}) VALUES ({', '.join(':' + c for c in params)})"
                " ON CONFLICT (run_id) DO NOTHING",
                params,
            )

    def begin_invocation(
        self,
        run_id: str,
        argv: Sequence[str],
        workers: int | None,
        *,
        lane: str | None = None,
    ) -> int:
        """Open the next invocation of ``run_id``; returns its sequence number (1, 2, ...)."""
        with self.transaction() as conn:
            project = self._run_project(run_id)
            seq = self.next_invocation_seq(run_id)
            conn.execute(
                "INSERT INTO invocations (project_id, run_id, seq, started_at, argv_json,"
                " worker_count, lane) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project, run_id, seq, utc_now(), dumps(list(argv)), workers, lane),
            )
        return int(seq)

    def next_invocation_seq(self, run_id: str) -> int:
        return int(
            self.conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM invocations WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        )

    def add_invocations(self, rows: Iterable[InvocationRecord]) -> int:
        added = 0
        with self.transaction() as conn:
            for inv in rows:
                cur = conn.execute(
                    "INSERT INTO invocations (project_id, run_id, seq, started_at, finished_at,"
                    " exit_status, argv_json, worker_count, lane)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (run_id, seq) DO NOTHING",
                    (
                        inv.project_id,
                        inv.run_id,
                        inv.seq,
                        inv.started_at,
                        inv.finished_at,
                        inv.exit_status,
                        dumps(list(inv.argv)),
                        inv.worker_count,
                        inv.lane,
                    ),
                )
                added += cur.rowcount
        return added

    def invocation_seq_by_start(
        self, run_id: str, started_at: str | None, argv: Sequence[str] = ()
    ) -> int | None:
        """The stored invocation of ``run_id`` with this start and argv (NULL-safe), if any."""
        row = self.conn.execute(
            "SELECT seq FROM invocations WHERE run_id = ? AND started_at IS ?"
            " AND COALESCE(argv_json, '[]') = ? ORDER BY seq LIMIT 1",
            (run_id, started_at, dumps(list(argv))),
        ).fetchone()
        return None if row is None else int(row[0])

    def invocation_attempts(self, run_id: str, seq: int) -> dict[str, list[int]]:
        """Stored attempt numbers per test of one invocation, ascending."""
        out: dict[str, list[int]] = {}
        rows = self.conn.execute(
            "SELECT tr.nodeid, tr.attempt FROM test_results tr"
            " JOIN invocations i ON i.id = tr.invocation_id"
            " WHERE i.run_id = ? AND i.seq = ? ORDER BY tr.nodeid, tr.attempt",
            (run_id, seq),
        )
        for nodeid, attempt in rows:
            out.setdefault(nodeid, []).append(int(attempt))
        return out

    def invocation_started(self, run_id: str, seq: int) -> str | None | bool:
        """The stored start of an invocation; ``False`` when there is no such invocation."""
        row = self.conn.execute(
            "SELECT started_at FROM invocations WHERE run_id = ? AND seq = ?", (run_id, seq)
        ).fetchone()
        return False if row is None else row[0]

    def finish_invocation(
        self, run_id: str, seq: int, exit_status: int | None, workers: int | None = None
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE invocations SET finished_at = ?, exit_status = ?,"
                " worker_count = COALESCE(?, worker_count) WHERE run_id = ? AND seq = ?",
                (utc_now(), exit_status, workers, run_id, seq),
            )

    def finish_run(
        self,
        run_id: str,
        status: str,
        integrity_verdict: str | None = None,
        *,
        finished_at: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            self._run_project(run_id)
            conn.execute(
                "UPDATE runs SET status = ?, finished_at = ?,"
                " integrity_verdict = COALESCE(?, integrity_verdict) WHERE run_id = ?",
                (status, finished_at or utc_now(), integrity_verdict, run_id),
            )

    def max_attempts(self, run_id: str) -> dict[str, int]:
        """Highest stored attempt per test of ``run_id`` (a later invocation continues it)."""
        rows = self.conn.execute(
            "SELECT nodeid, MAX(attempt) FROM test_results WHERE run_id = ? GROUP BY nodeid",
            (run_id,),
        )
        return {nodeid: int(attempt) for nodeid, attempt in rows}

    # ------------------------------------------------------------------ cases

    def upsert_cases(self, cases: Iterable[CaseRecord]) -> None:
        now = utc_now()
        with self.transaction() as conn:
            for case in cases:
                self.ensure_project(case.project_id)
                # A pinned id moving onto a key another row still holds: the old row is a
                # superseded identity, not a second live case.
                conn.execute(
                    "UPDATE cases SET status = 'removed',"
                    " case_key = case_key || ' [superseded by ' || ? || ']'"
                    " WHERE project_id = ? AND case_key = ? AND case_id <> ?",
                    (case.case_id, case.project_id, case.case_key, case.case_id),
                )
                conn.execute(
                    """
                    INSERT INTO cases (project_id, case_id, case_key, framework, title, module,
                        qualname, file_path, line, description, owner, priority, markers_json,
                        status, first_seen_at, last_seen_at, current_revision_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (project_id, case_id) DO UPDATE SET
                        case_key = excluded.case_key,
                        framework = excluded.framework,
                        title = excluded.title,
                        module = excluded.module,
                        qualname = excluded.qualname,
                        file_path = excluded.file_path,
                        line = excluded.line,
                        description = excluded.description,
                        owner = excluded.owner,
                        priority = excluded.priority,
                        markers_json = excluded.markers_json,
                        status = excluded.status,
                        first_seen_at = MIN(COALESCE(cases.first_seen_at, excluded.first_seen_at),
                                            COALESCE(excluded.first_seen_at, cases.first_seen_at)),
                        last_seen_at = MAX(COALESCE(cases.last_seen_at, ''),
                                           COALESCE(excluded.last_seen_at, '')),
                        current_revision_id = COALESCE(excluded.current_revision_id,
                                                       cases.current_revision_id)
                    """,
                    (
                        case.project_id,
                        case.case_id,
                        case.case_key,
                        case.framework,
                        case.title,
                        case.module,
                        case.qualname,
                        case.file_path,
                        case.line,
                        case.description,
                        case.owner,
                        case.priority,
                        dumps(list(case.markers)),
                        case.status,
                        case.first_seen_at or now,
                        case.last_seen_at or now,
                        case.current_revision_id,
                    ),
                )
                for kind, ref in case.links:
                    self._link(case.project_id, case.case_id, kind, ref, "marker")

    def _link(self, project: str, case_id: str, kind: str, ref: str, source: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO case_links (project_id, case_id, kind, ref, source, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (project_id, case_id, kind, ref) DO NOTHING",
            (project, case_id, kind, ref, source, utc_now()),
        )
        return cur.rowcount

    def add_links(
        self,
        links: Iterable[tuple[str, str, str]],
        *,
        source: str = "manual",
        project_id: str = DEFAULT_PROJECT,
    ) -> int:
        """Add ``(case_id, kind, ref)`` links; returns how many were new."""
        with self.transaction():
            return sum(self._link(project_id, c, k, r, source) for c, k, r in links)

    def mark_removed(
        self, present_case_ids: Iterable[str], project_id: str = DEFAULT_PROJECT
    ) -> int:
        """Mark every active case of ``project_id`` not in ``present_case_ids`` as removed.

        Only call this after a *full* collection: a filtered run would "delete" whatever the
        filter left out.
        """
        present = set(present_case_ids)
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT case_id FROM cases WHERE project_id = ? AND status = 'active'",
                (project_id,),
            ).fetchall()
            gone = [r[0] for r in rows if r[0] not in present]
            conn.executemany(
                "UPDATE cases SET status = 'removed' WHERE project_id = ? AND case_id = ?",
                [(project_id, c) for c in gone],
            )
        return len(gone)

    # ------------------------------------------------------------------ revisions

    def add_revisions(self, rows: Iterable[RevisionRecord]) -> int:
        """Store revisions not seen before; returns how many were new."""
        added = 0
        with self.transaction() as conn:
            for rev in rows:
                cur = conn.execute(
                    "INSERT INTO case_revisions (project_id, revision_id, case_id, source_hash,"
                    " file_blob_sha, git_sha, steps_json, first_seen_run_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (project_id, case_id, revision_id) DO NOTHING",
                    (
                        rev.project_id,
                        rev.revision_id,
                        rev.case_id,
                        rev.source_hash,
                        rev.file_blob_sha,
                        rev.git_sha,
                        dumps(list(rev.steps)),
                        rev.first_seen_run_id,
                        rev.created_at or utc_now(),
                    ),
                )
                added += cur.rowcount
                if cur.rowcount:  # only a newly seen revision becomes the current one
                    conn.execute(
                        "UPDATE cases SET current_revision_id = ?"
                        " WHERE project_id = ? AND case_id = ?",
                        (rev.revision_id, rev.project_id, rev.case_id),
                    )
        return added

    # ------------------------------------------------------------------ results

    def add_results(self, rows: Iterable[ResultRecord]) -> int:
        """Insert result attempts; an attempt already stored is left alone (idempotent)."""
        added = 0
        invocation_ids: dict[tuple[str, int], int | None] = {}
        with self.transaction() as conn:
            for row in rows:
                inv_id = None
                if row.invocation is not None:
                    key = (row.run_id, row.invocation)
                    if key not in invocation_ids:
                        found = conn.execute(
                            "SELECT id FROM invocations WHERE run_id = ? AND seq = ?", key
                        ).fetchone()
                        invocation_ids[key] = found[0] if found else None
                    inv_id = invocation_ids[key]
                try:
                    cur = conn.execute(
                        """
                        INSERT INTO test_results (project_id, run_id, invocation_id, case_id,
                            revision_id, nodeid, param_id, attempt, outcome, is_cleared,
                            duration_ms, setup_ms, call_ms, teardown_ms, started_at, finished_at,
                            worker, browser, shard_index, message, longrepr, failure_fingerprint,
                            skip_reason, defect_ref, markers_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?)
                        ON CONFLICT (run_id, nodeid, attempt) DO NOTHING
                        """,
                        (
                            row.project_id,
                            row.run_id,
                            inv_id,
                            row.case_id,
                            row.revision_id,
                            row.nodeid,
                            row.param_id,
                            row.attempt,
                            row.outcome,
                            1 if row.is_cleared else 0,
                            row.duration_ms,
                            row.setup_ms,
                            row.call_ms,
                            row.teardown_ms,
                            row.started_at,
                            row.finished_at,
                            row.worker,
                            row.browser,
                            row.shard_index,
                            truncate(row.message, MESSAGE_LIMIT),
                            truncate(row.longrepr, LONGREPR_LIMIT),
                            row.failure_fingerprint,
                            row.skip_reason,
                            row.defect_ref,
                            dumps(list(row.markers)) if row.markers else None,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise StoreError(f"cannot store result {row.nodeid!r}: {exc}") from exc
                added += cur.rowcount
        return added

    def _result_id(self, run_id: str, nodeid: str, attempt: int | None) -> int | None:
        if attempt is None:
            found = self.conn.execute(
                "SELECT id FROM final_results WHERE run_id = ? AND nodeid = ?", (run_id, nodeid)
            ).fetchone()
        else:
            found = self.conn.execute(
                "SELECT id FROM test_results WHERE run_id = ? AND nodeid = ? AND attempt = ?",
                (run_id, nodeid, attempt),
            ).fetchone()
        return found[0] if found else None

    def add_evidence(self, rows: Iterable[EvidenceRecord]) -> int:
        added = 0
        with self.transaction() as conn:
            for ev in rows:
                result_id = None
                attempt = ev.attempt
                if ev.nodeid:
                    if attempt is None:
                        result_id = self._result_id(ev.run_id, ev.nodeid, None)
                        if result_id is not None:
                            attempt = conn.execute(
                                "SELECT attempt FROM test_results WHERE id = ?", (result_id,)
                            ).fetchone()[0]
                    else:
                        result_id = self._result_id(ev.run_id, ev.nodeid, attempt)
                cur = conn.execute(
                    "INSERT INTO evidence (project_id, run_id, result_id, nodeid, attempt, kind,"
                    " uri, sha256, bytes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (run_id, nodeid, attempt, kind, uri) DO NOTHING",
                    (
                        ev.project_id,
                        ev.run_id,
                        result_id,
                        _blank(ev.nodeid),
                        attempt or 0,
                        ev.kind,
                        ev.uri,
                        ev.sha256,
                        ev.bytes,
                        ev.created_at or utc_now(),
                    ),
                )
                added += cur.rowcount
        return added

    # ------------------------------------------------------------------ side channels

    def add_verdicts(self, rows: Iterable[VerdictRecord]) -> int:
        added = 0
        with self.transaction() as conn:
            for v in rows:
                cur = conn.execute(
                    "INSERT INTO verdicts (project_id, run_id, nodeid, kind, ticket, verdict,"
                    " detail, subject, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (run_id, nodeid, kind, ticket, verdict, detail, subject,"
                    " recorded_at) DO NOTHING",
                    (
                        v.project_id,
                        v.run_id,
                        v.nodeid,
                        v.kind,
                        _blank(v.ticket),
                        v.verdict,
                        v.detail or "",
                        _blank(v.subject),
                        _blank(v.recorded_at),
                    ),
                )
                added += cur.rowcount
        return added

    def ingest_verdicts(self, path: str | Path | None, run_id: str) -> int:
        """Mirror this run's ``verdicts.jsonl`` rows; ingesting twice adds nothing."""
        from verification.verdicts import read_verdicts

        project = self._run_project(run_id)
        rows = [
            replace(VerdictRecord.from_row(r, run_id), project_id=project)
            for r in read_verdicts(path, run_id=run_id)
            if r.get("kind") in ("expected_red", "coverage") and r.get("nodeid")
        ]
        return self.add_verdicts(rows)

    def add_provenance(self, rows: Iterable[ProvenanceRecord]) -> int:
        added = 0
        with self.transaction() as conn:
            for p in rows:
                cur = conn.execute(
                    "INSERT INTO data_origin (project_id, run_id, nodeid, origin, fixture,"
                    " entity_id, candidates, attributes_json, recorded_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (run_id, nodeid, origin, fixture, entity_id, recorded_at)"
                    " DO NOTHING",
                    (
                        p.project_id,
                        p.run_id,
                        p.nodeid,
                        p.origin,
                        p.fixture,
                        _blank(p.entity_id),
                        p.candidates,
                        dumps(p.attributes or {}),
                        _blank(p.recorded_at),
                    ),
                )
                added += cur.rowcount
        return added

    def ingest_provenance(self, path: str | Path | None, run_id: str) -> int:
        """Mirror this run's data-origin rows; ingesting twice adds nothing."""
        from gate.provenance import read_rows

        project = self._run_project(run_id)
        return self.add_provenance(
            ProvenanceRecord(
                run_id=run_id,
                nodeid=r.nodeid,
                origin=r.origin,
                fixture=r.fixture,
                entity_id=r.entity_id,
                candidates=r.candidates,
                attributes=dict(r.attributes),
                recorded_at=r.timestamp or None,
                project_id=project,
            )
            for r in read_rows(path, run_id=run_id)
        )

    # ------------------------------------------------------------------ reads

    def query(self, sql_name: str, **params: Any) -> list[dict[str, Any]]:
        """Run the named query; parameters it uses but the caller omitted are NULL."""
        sql = _query_sql(sql_name)
        bound = {name: None for name in _PARAM.findall(_strip_comments(sql))}
        unknown = set(params) - set(bound)
        if unknown:
            raise StoreError(f"query {sql_name!r} takes no parameter(s) {sorted(unknown)}")
        bound.update(params)
        return [dict(r) for r in self.conn.execute(sql, bound).fetchall()]

    def run(self, run_id: str) -> RunRecord | None:
        rows = self.query("run_get", run_id=run_id)
        return row_to_run(rows[0]) if rows else None

    # ------------------------------------------------------------------ maintenance

    def prune(self, *, keep_runs: int | None = None, older_than: str | None = None) -> int:
        """Delete whole runs (results, evidence links, verdicts cascade); returns runs removed."""
        with self.transaction() as conn:
            doomed: set[str] = set()
            if keep_runs is not None:
                doomed |= {
                    r[0]
                    for r in conn.execute(
                        "SELECT run_id FROM runs ORDER BY started_at DESC, id DESC"
                        " LIMIT -1 OFFSET ?",
                        (max(keep_runs, 0),),
                    )
                }
            if older_than is not None:
                doomed |= {
                    r[0]
                    for r in conn.execute(
                        "SELECT run_id FROM runs WHERE started_at < ?", (older_than,)
                    )
                }
            conn.executemany("DELETE FROM runs WHERE run_id = ?", [(r,) for r in doomed])
        return len(doomed)


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def row_to_run(row: dict[str, Any]) -> RunRecord:
    data = dict(row)
    data["provenance"] = loads(data.pop("provenance_json", None))
    data["metadata"] = loads(data.pop("metadata_json", None))
    return RunRecord.from_json(data)


def row_to_result(row: dict[str, Any]) -> ResultRecord:
    data = dict(row)
    data["markers"] = tuple(loads(data.pop("markers_json", None), []) or ())
    data["is_cleared"] = bool(data.get("is_cleared"))
    data["invocation"] = data.pop("invocation_seq", None)
    return ResultRecord.from_json(data)


def row_to_case(row: dict[str, Any], links: Sequence[tuple[str, str]] = ()) -> CaseRecord:
    data = dict(row)
    data["markers"] = tuple(loads(data.pop("markers_json", None), []) or ())
    data["links"] = tuple(tuple(x) for x in links)
    data["steps"] = tuple(loads(data.pop("steps_json", None), []) or ())
    return CaseRecord.from_json(data)


def open_store(url: str | None = None, *, readonly: bool = False) -> ResultsStore:
    """Open the store at ``url`` (a path, ``sqlite:///path`` or ``:memory:``).

    Default: ``$RESULTS_DB``, else ``$RESULTS_DIR/results.db``, else
    ``reports/results/results.db``. Opening for writing applies pending migrations.
    """
    target = url or str(default_db_path())
    if target.startswith("sqlite:///"):
        target = target[len("sqlite:///") :]
    elif "://" in target:
        raise StoreError(f"unsupported store url {target!r}: only SQLite is implemented")
    return SqliteResultsStore(target, readonly=readonly)


__all__ = [
    "OPEN_LOG_ENV",
    "ResultsStore",
    "SqliteResultsStore",
    "StoreError",
    "open_store",
    "query_names",
    "register_queries",
    "row_to_case",
    "row_to_result",
    "row_to_run",
]
