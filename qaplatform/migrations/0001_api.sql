-- requires: core>=1
-- qaplatform 0001: API tokens, run requests and idempotency keys.
-- Tables carry the `qap_` prefix (the domain's prefix) and reference core tables by key.

-- An API token: only its SHA-256 is stored; the raw value is shown once at issue.
-- project_id NULL = an instance token (bootstrap admin), valid for every project.
CREATE TABLE qap_tokens (
    id           TEXT PRIMARY KEY,
    project_id   TEXT REFERENCES projects (id),
    user_id      TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('viewer', 'tester', 'lead', 'admin')),
    token_hash   TEXT NOT NULL UNIQUE,
    label        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);

CREATE INDEX qap_tokens_project ON qap_tokens (project_id);

-- A request to start a run, handed to a dispatcher (CI workflow dispatch, ...).
CREATE TABLE qap_run_requests (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects (id),
    requested_by TEXT NOT NULL,
    dispatcher   TEXT NOT NULL,
    target       TEXT NOT NULL,
    environment  TEXT,
    release      TEXT,
    params_json  TEXT,
    status       TEXT NOT NULL CHECK (status IN (
                     'queued', 'dispatched', 'running', 'finished', 'failed', 'rejected')),
    detail       TEXT,
    external_ref TEXT,
    run_id       TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX qap_run_requests_project ON qap_run_requests (project_id, created_at);

-- A replayed POST with the same Idempotency-Key returns the first response.
CREATE TABLE qap_idempotency_keys (
    project_id    TEXT NOT NULL REFERENCES projects (id),
    key           TEXT NOT NULL,
    endpoint      TEXT NOT NULL,
    status        INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (project_id, key)
);
