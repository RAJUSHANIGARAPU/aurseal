-- requires: qaplatform>=1
-- qaplatform 0002: the append-only, hash-chained audit log.
-- row_hash = sha256(prev_hash || canonical JSON of the row), chained per project, so an
-- edited, deleted or reordered row is detectable by verification, not only forbidden.
-- The triggers refuse UPDATE and DELETE: defence in depth, not the guarantee (anyone with
-- write access to the file can drop a trigger; the chain is what notices).

CREATE TABLE qap_audit_log (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL REFERENCES projects (id),
    ts            TEXT NOT NULL,
    actor_user_id TEXT NOT NULL,
    token_id      TEXT,
    action        TEXT NOT NULL,
    target_type   TEXT NOT NULL,
    target_id     TEXT NOT NULL DEFAULT '',
    diff_json     TEXT NOT NULL DEFAULT '{}',
    request_id    TEXT NOT NULL DEFAULT '',
    source_ip     TEXT,
    prev_hash     TEXT NOT NULL,
    row_hash      TEXT NOT NULL UNIQUE
);

CREATE INDEX qap_audit_log_project_ts ON qap_audit_log (project_id, ts);

CREATE TRIGGER qap_audit_log_no_update BEFORE UPDATE ON qap_audit_log
BEGIN
    SELECT RAISE(ABORT, 'qap_audit_log is append-only');
END;

CREATE TRIGGER qap_audit_log_no_delete BEFORE DELETE ON qap_audit_log
BEGIN
    SELECT RAISE(ABORT, 'qap_audit_log is append-only');
END;
