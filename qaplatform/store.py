"""Platform storage over the results store.

Two levels, on purpose:

* :class:`PlatformStore` opens the results database, applies the platform's own
  migration domain (tables ``qap_*``) and hands out :class:`ScopedStore` objects. It holds
  the few unscoped operations that must happen *before* a project is known (token lookup,
  the project registry).
* :class:`ScopedStore` is bound to one project at construction. None of its methods takes
  a project argument, every named query it runs filters on ``:project_id`` (asserted by
  a test over ``qaplatform/queries/``), and every row it writes carries its project. A
  cross-project read is therefore not expressible through it.

Only :mod:`qaplatform.service` uses this module; the HTTP and UI adapters never import it
(asserted by a source scan).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from results import migrations
from results._util import dumps, loads, utc_now
from results.models import (
    CaseRecord,
    EvidenceRecord,
    ResultRecord,
    RevisionRecord,
    RunRecord,
    VerdictRecord,
)
from results.store import SqliteResultsStore, open_store, register_queries

HERE = Path(__file__).resolve().parent
DOMAIN = "qaplatform"
PREFIX = "qap"
MIGRATIONS_DIR = HERE / "migrations"
QUERIES_DIR = HERE / "queries"


def _apply_platform_migrations(conn: sqlite3.Connection) -> None:
    """Apply the ``qaplatform`` domain, registered only for the duration of the call so
    other stores opened in this process are unaffected."""
    migrations.register_domain(DOMAIN, MIGRATIONS_DIR, PREFIX)
    try:
        migrations.apply(conn, [DOMAIN])
    finally:
        migrations.unregister_domain(DOMAIN)


class PlatformStore:
    """The results store plus the platform's tables; hands out project-bound views."""

    def __init__(self, results: SqliteResultsStore) -> None:
        self.results = results
        self.conn = results.conn
        register_queries(QUERIES_DIR)
        if not results.readonly:
            _apply_platform_migrations(self.conn)

    @classmethod
    def open(cls, url: str | None = None) -> PlatformStore:
        """Open (and migrate) the store at ``url``; default as :func:`results.store.open_store`."""
        results = open_store(url)
        assert isinstance(results, SqliteResultsStore)
        return cls(results)

    def close(self) -> None:
        self.results.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.results.transaction() as conn:
            yield conn

    def scoped(self, project_id: str) -> ScopedStore:
        return ScopedStore(self, project_id)

    def ready(self) -> bool:
        """Store reachable and the platform tables present."""
        try:
            self.conn.execute("SELECT 1 FROM qap_tokens LIMIT 1").fetchall()
            return True
        except sqlite3.Error:
            return False

    # ------------------------------------------------------------------ projects (unscoped)

    def project(self, project_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None

    def projects(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM projects ORDER BY id")]

    def projects_of(self, user_id: str) -> list[dict[str, Any]]:
        """Projects a user holds a binding in, with that role."""
        rows = self.conn.execute(
            "SELECT p.*, b.role FROM projects p JOIN qap_role_bindings b"
            " ON b.project_id = p.id WHERE b.user_id = ? ORDER BY p.id",
            (user_id,),
        )
        return [dict(r) for r in rows]

    def create_project(self, key: str, name: str, settings: dict[str, Any] | None) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO projects (id, project_key, name, settings_json) VALUES (?, ?, ?, ?)",
                (key, key, name or key, dumps(settings or {})),
            )

    # ------------------------------------------------------------------ tokens (unscoped)

    def insert_token(self, row: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO qap_tokens (id, project_id, user_id, role, token_hash, label,"
                " created_at, expires_at) VALUES (:id, :project_id, :user_id, :role,"
                " :token_hash, :label, :created_at, :expires_at)",
                row,
            )

    def token_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM qap_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        return dict(row) if row else None

    def token(self, token_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM qap_tokens WHERE id = ?", (token_id,)).fetchone()
        return dict(row) if row else None

    def tokens(self, project_id: str | None) -> list[dict[str, Any]]:
        """Tokens of one project (``None``: instance tokens). Hashes are never returned."""
        sql = (
            "SELECT id, project_id, user_id, role, label, created_at, expires_at, last_used_at,"
            " revoked_at FROM qap_tokens WHERE project_id IS ? ORDER BY created_at"
        )
        return [dict(r) for r in self.conn.execute(sql, (project_id,))]

    def touch_token(self, token_id: str, now: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE qap_tokens SET last_used_at = ? WHERE id = ?", (now, token_id))

    def revoke_token(self, token_id: str, now: str) -> bool:
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE qap_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, token_id),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------ users (unscoped)

    def user(self, user_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM qap_users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def ensure_user(
        self, user_id: str, display_name: str = "", *, instance_admin: bool = False
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO qap_users (id, display_name, is_instance_admin, created_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET"
                " is_instance_admin = MAX(qap_users.is_instance_admin, excluded.is_instance_admin)",
                (user_id, display_name or user_id, 1 if instance_admin else 0, utc_now()),
            )
        user = self.user(user_id)
        assert user is not None
        return user

    def binding(self, user_id: str, project_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM qap_role_bindings WHERE user_id = ? AND project_id = ?",
            (user_id, project_id),
        ).fetchone()
        return dict(row) if row else None


class ScopedStore:
    """Every read and write of one project. There is no way to name another project here."""

    def __init__(self, store: PlatformStore, project_id: str) -> None:
        self._store = store
        self._results = store.results
        self.project_id = project_id

    def _q(self, name: str, **params: Any) -> list[dict[str, Any]]:
        return self._results.query(name, project_id=self.project_id, **params)

    def _own(self, record: Any) -> Any:
        return replace(record, project_id=self.project_id)

    # ------------------------------------------------------------------ the project itself

    def project(self) -> dict[str, Any]:
        row = self._store.project(self.project_id)
        if row is None:
            raise KeyError(self.project_id)
        return row

    def update_project(self, **fields: Any) -> None:
        allowed = {"name", "archived_at", "settings_json"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        if not fields:
            return
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        with self._store.transaction() as conn:
            conn.execute(
                f"UPDATE projects SET {sets} WHERE id = :project_id",
                {**fields, "project_id": self.project_id},
            )

    # ------------------------------------------------------------------ release evidence

    def release_evidence(self, release: str) -> Any:
        """This project's runs labelled ``release``, as a traceability result source."""
        from traceability.evidence import Scope, StoreResultSource

        return StoreResultSource(self._results, Scope(release=release), project_id=self.project_id)

    def requirement_registry(self) -> Any:
        """This project's stored requirement registry (empty when none was synced)."""
        from traceability.store import load_registry

        return load_registry(self._results, project_id=self.project_id)

    # ------------------------------------------------------------------ runs

    def run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._q("qap_run_get", run_id=run_id)
        return rows[0] if rows else None

    def run_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        rows = self._q("qap_run_by_external", external_id=external_id)
        return rows[0] if rows else None

    def runs(
        self,
        *,
        status: str | None = None,
        branch: str | None = None,
        source: str | None = None,
        since: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._q(
            "qap_runs_list",
            status=status,
            branch=branch,
            source=source,
            since=since,
            limit=limit,
            offset=offset,
        )

    def create_run(self, run: RunRecord) -> None:
        self._results.begin_run(self._own(run))

    def finish_run(
        self, run_id: str, status: str, verdict: str | None, finished_at: str | None
    ) -> None:
        if self.run(run_id) is None:
            raise KeyError(run_id)
        self._results.finish_run(run_id, status, verdict, finished_at=finished_at)

    def set_expected_count(self, run_id: str, expected: int) -> None:
        with self._store.transaction() as conn:
            conn.execute(
                "UPDATE runs SET expected_count = ? WHERE project_id = ? AND run_id = ?",
                (expected, self.project_id, run_id),
            )

    # ------------------------------------------------------------------ results

    def upsert_cases(self, cases: Iterable[CaseRecord]) -> None:
        self._results.upsert_cases(self._own(c) for c in cases)

    def add_results(self, rows: Iterable[ResultRecord]) -> int:
        return self._results.add_results(self._own(r) for r in rows)

    def add_evidence(self, rows: Iterable[EvidenceRecord]) -> int:
        return self._results.add_evidence(self._own(e) for e in rows)

    def add_verdicts(self, rows: Iterable[VerdictRecord]) -> int:
        return self._results.add_verdicts(self._own(v) for v in rows)

    def final_results(
        self, run_id: str, *, outcome: str | None = None, limit: int = -1, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._q(
            "qap_final_results", run_id=run_id, outcome=outcome, limit=limit, offset=offset
        )

    def all_results(
        self, run_id: str, *, outcome: str | None = None, limit: int = -1, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._q(
            "qap_results_all", run_id=run_id, outcome=outcome, limit=limit, offset=offset
        )

    def run_verdicts(self, run_id: str) -> list[dict[str, Any]]:
        return self._q("qap_run_verdicts", run_id=run_id)

    def run_evidence(self, run_id: str) -> list[dict[str, Any]]:
        return self._q("qap_run_evidence", run_id=run_id)

    def verdicts_page(
        self,
        *,
        run_id: str | None,
        ticket: str | None,
        kind: str | None,
        after: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self._q(
            "qap_verdicts_page", run_id=run_id, ticket=ticket, kind=kind, after=after, limit=limit
        )

    def case_history(self, case_id: str, *, limit: int = -1) -> list[dict[str, Any]]:
        return self._q("qap_case_history", case_id=case_id, limit=limit)

    # ------------------------------------------------------------------ traceability inputs

    def requirement_links(self, requirement: str | None = None) -> list[dict[str, Any]]:
        return self._q("qap_requirement_links", requirement=requirement)

    def defect_links(self) -> list[dict[str, Any]]:
        return self._q("qap_defect_links")

    def latest_results(self, release: str | None = None) -> list[dict[str, Any]]:
        return self._q("qap_latest_results", release=release)

    def coverage_verdicts(self, release: str | None = None) -> list[dict[str, Any]]:
        return self._q("qap_coverage_verdicts", release=release)

    # ------------------------------------------------------------------ run requests

    def insert_run_request(self, row: dict[str, Any]) -> None:
        data = {**row, "project_id": self.project_id, "params_json": dumps(row.get("params"))}
        data.pop("params", None)
        with self._store.transaction() as conn:
            conn.execute(
                "INSERT INTO qap_run_requests (id, project_id, requested_by, dispatcher, target,"
                " environment, release, params_json, status, detail, external_ref, run_id,"
                " created_at, updated_at) VALUES (:id, :project_id, :requested_by, :dispatcher,"
                " :target, :environment, :release, :params_json, :status, :detail,"
                " :external_ref, :run_id, :created_at, :updated_at)",
                data,
            )

    def update_run_request(self, request_id: str, **fields: Any) -> None:
        allowed = {"status", "detail", "external_ref", "run_id"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        with self._store.transaction() as conn:
            conn.execute(
                f"UPDATE qap_run_requests SET {sets}, updated_at = :updated_at"
                " WHERE project_id = :project_id AND id = :id",
                {
                    **fields,
                    "updated_at": utc_now(),
                    "project_id": self.project_id,
                    "id": request_id,
                },
            )

    def run_request(self, request_id: str) -> dict[str, Any] | None:
        rows = self._q("qap_run_request_get", id=request_id)
        if not rows:
            return None
        row = rows[0]
        row["params"] = loads(row.pop("params_json", None), {}) or {}
        return row

    def run_requests(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._q("qap_run_requests_list", limit=limit)
        for row in rows:
            row["params"] = loads(row.pop("params_json", None), {}) or {}
        return rows

    # ------------------------------------------------------------------ members and tokens

    def members(self) -> list[dict[str, Any]]:
        return self._q("qap_members")

    def member(self, user_id: str) -> dict[str, Any] | None:
        rows = self._q("qap_member", user_id=user_id)
        return rows[0] if rows else None

    def grant(self, user_id: str, role: str, granted_by: str) -> None:
        with self._store.transaction() as conn:
            conn.execute(
                "INSERT INTO qap_role_bindings (user_id, project_id, role, granted_by,"
                " granted_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (user_id, project_id)"
                " DO UPDATE SET role = excluded.role, granted_by = excluded.granted_by,"
                " granted_at = excluded.granted_at",
                (user_id, self.project_id, role, granted_by, utc_now()),
            )

    def remove_member(self, user_id: str) -> int:
        with self._store.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM qap_role_bindings WHERE project_id = ? AND user_id = ?",
                (self.project_id, user_id),
            )
        return cur.rowcount

    def project_tokens(self) -> list[dict[str, Any]]:
        return self._q("qap_tokens_list")

    def token(self, token_id: str) -> dict[str, Any] | None:
        row = self._store.token(token_id)
        return row if row and row["project_id"] == self.project_id else None

    def revoke_tokens(self, *, token_id: str | None = None, user_id: str | None = None) -> int:
        """Revoke one token, or every active token a user holds in this project."""
        clause, value = ("id = ?", token_id) if token_id else ("user_id = ?", user_id)
        with self._store.transaction() as conn:
            cur = conn.execute(
                f"UPDATE qap_tokens SET revoked_at = ? WHERE project_id = ? AND {clause}"
                " AND revoked_at IS NULL",
                (utc_now(), self.project_id, value),
            )
        return cur.rowcount

    # ------------------------------------------------------------------ manual executions

    def add_revisions(self, rows: Iterable[RevisionRecord]) -> int:
        return self._results.add_revisions(self._own(r) for r in rows)

    def insert_manual_execution(self, row: dict[str, Any]) -> None:
        with self._store.transaction() as conn:
            conn.execute(
                "INSERT INTO qap_manual_executions (id, project_id, run_id, case_id, case_key,"
                " revision_id, title, steps_json, expected_json, requirements_json,"
                " tester_user_id, status, started_at) VALUES (:id, :project_id, :run_id,"
                " :case_id, :case_key, :revision_id, :title, :steps_json, :expected_json,"
                " :requirements_json, :tester_user_id, 'in_progress', :started_at)",
                {**row, "project_id": self.project_id},
            )

    def manual_executions(self, run_id: str) -> list[dict[str, Any]]:
        return [_manual(r) for r in self._q("qap_manual_executions", run_id=run_id)]

    def manual_execution(self, execution_id: str) -> dict[str, Any] | None:
        rows = self._q("qap_manual_execution", id=execution_id)
        return _manual(rows[0]) if rows else None

    def manual_steps(self, execution_id: str) -> list[dict[str, Any]]:
        return self._q("qap_manual_steps", id=execution_id)

    def record_manual_step(self, execution_id: str, row: dict[str, Any]) -> None:
        if self.manual_execution(execution_id) is None:
            raise KeyError(execution_id)
        with self._store.transaction() as conn:
            conn.execute(
                "INSERT INTO qap_manual_step_results (execution_id, step_no, status, actual,"
                " evidence_uri, recorded_by, recorded_at) VALUES (:execution_id, :step_no,"
                " :status, :actual, :evidence_uri, :recorded_by, :recorded_at)"
                " ON CONFLICT (execution_id, step_no) DO UPDATE SET status = excluded.status,"
                " actual = excluded.actual, evidence_uri = excluded.evidence_uri,"
                " recorded_by = excluded.recorded_by, recorded_at = excluded.recorded_at",
                {**row, "execution_id": execution_id},
            )

    def finish_manual_execution(self, execution_id: str, status: str, finished_at: str) -> None:
        with self._store.transaction() as conn:
            conn.execute(
                "UPDATE qap_manual_executions SET status = ?, finished_at = ?"
                " WHERE project_id = ? AND id = ?",
                (status, finished_at, self.project_id, execution_id),
            )

    # ------------------------------------------------------------------ exploratory sessions

    def insert_session(self, row: dict[str, Any]) -> None:
        with self._store.transaction() as conn:
            conn.execute(
                "INSERT INTO qap_sessions (id, project_id, run_id, charter, area,"
                " requirements_json, timebox_min, tester_user_id, status, started_at)"
                " VALUES (:id, :project_id, :run_id, :charter, :area, :requirements_json,"
                " :timebox_min, :tester_user_id, 'active', :started_at)",
                {**row, "project_id": self.project_id},
            )

    def session(self, session_id: str) -> dict[str, Any] | None:
        rows = self._q("qap_session", id=session_id)
        return _session(rows[0]) if rows else None

    def sessions(self, *, requirement: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return [_session(r) for r in self._q("qap_sessions", requirement=requirement, limit=limit)]

    def session_notes(self, session_id: str) -> list[dict[str, Any]]:
        return self._q("qap_session_notes", id=session_id)

    def add_session_note(self, session_id: str, row: dict[str, Any]) -> int:
        if self.session(session_id) is None:
            raise KeyError(session_id)
        with self._store.transaction() as conn:
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM qap_session_notes WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO qap_session_notes (session_id, seq, ts, kind, text,"
                " attachment_uri, defect_key, author) VALUES (:session_id, :seq, :ts, :kind,"
                " :text, :attachment_uri, :defect_key, :author)",
                {**row, "session_id": session_id, "seq": seq},
            )
        return int(seq)

    def end_session(self, session_id: str, status: str, ended_at: str, debrief: str) -> None:
        with self._store.transaction() as conn:
            conn.execute(
                "UPDATE qap_sessions SET status = ?, ended_at = ?, debrief = ?"
                " WHERE project_id = ? AND id = ?",
                (status, ended_at, debrief, self.project_id, session_id),
            )

    # ------------------------------------------------------------------ audit log

    def audit_head(self) -> dict[str, Any] | None:
        rows = self._q("qap_audit_head")
        return rows[0] if rows else None

    def audit_next_seq(self) -> int:
        # seq is one sequence across projects; called inside the writing transaction.
        row = self._store.conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM qap_audit_log")
        return int(row.fetchone()[0])

    def audit_append(self, row: dict[str, Any]) -> None:
        if row["project_id"] != self.project_id:
            raise ValueError("audit row for another project")
        with self._store.transaction() as conn:
            conn.execute(
                "INSERT INTO qap_audit_log (seq, project_id, ts, actor_user_id, token_id,"
                " action, target_type, target_id, diff_json, request_id, source_ip,"
                " prev_hash, row_hash) VALUES (:seq, :project_id, :ts, :actor_user_id,"
                " :token_id, :action, :target_type, :target_id, :diff_json, :request_id,"
                " :source_ip, :prev_hash, :row_hash)",
                row,
            )

    def audit_chain(self) -> list[dict[str, Any]]:
        return self._q("qap_audit_chain")

    def audit_page(
        self,
        *,
        action: str | None,
        actor: str | None,
        since: str | None,
        after: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self._q(
            "qap_audit_page", action=action, actor=actor, since=since, after=after, limit=limit
        )

    # ------------------------------------------------------------------ idempotency

    def stored_response(self, key: str) -> dict[str, Any] | None:
        rows = self._q("qap_idempotency_get", key=key)
        return rows[0] if rows else None

    def store_response(self, key: str, endpoint: str, status: int, response: Any) -> None:
        with self._store.transaction() as conn:
            conn.execute(
                "INSERT INTO qap_idempotency_keys (project_id, key, endpoint, status,"
                " response_json, created_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (project_id, key) DO NOTHING",
                (self.project_id, key, endpoint, status, dumps(response), utc_now()),
            )

    def purge_responses(self, before: str) -> int:
        with self._store.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM qap_idempotency_keys WHERE project_id = ? AND created_at < ?",
                (self.project_id, before),
            )
        return cur.rowcount


def _session(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["requirements"] = loads(out.pop("requirements_json", None), []) or []
    return out


def _manual(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("steps", "expected", "requirements"):
        out[key] = loads(out.pop(f"{key}_json", None), []) or []
    return out


__all__ = ["DOMAIN", "PREFIX", "PlatformStore", "ScopedStore"]
