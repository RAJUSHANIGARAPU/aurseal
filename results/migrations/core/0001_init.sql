-- core 0001: the complete first schema.
-- Conventions: plural snake_case tables; surrogate `id INTEGER PRIMARY KEY` plus a UNIQUE
-- natural key; `*_at` ISO-8601 UTC text; `*_json` JSON text; `is_*` 0/1; enums as CHECKed text.
-- Every data table carries `project_id` (tenant; 'default' for single-project installs).

CREATE TABLE projects (
    id            TEXT PRIMARY KEY,
    project_key   TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    archived_at   TEXT,
    settings_json TEXT
);

INSERT INTO projects (id, project_key, name) VALUES ('default', 'default', 'Default project');

CREATE TABLE runs (
    id                INTEGER PRIMARY KEY,
    project_id        TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    run_id            TEXT NOT NULL UNIQUE,
    external_id       TEXT,
    name              TEXT,
    source            TEXT NOT NULL DEFAULT 'pytest' CHECK (source IN (
                          'pytest', 'junit-import', 'api', 'bundle',
                          'automated', 'manual', 'exploratory', 'import')),
    framework         TEXT,
    lane              TEXT,
    environment       TEXT,
    sut_version       TEXT,
    git_sha           TEXT,
    git_branch        TEXT,
    ci_provider       TEXT,
    ci_url            TEXT,
    trigger           TEXT,
    schedule_name     TEXT,
    run_request_id    TEXT,
    release_label     TEXT,
    expected_count    INTEGER CHECK (expected_count IS NULL OR expected_count >= 0),
    host              TEXT,
    started_at        TEXT,
    finished_at       TEXT,
    status            TEXT NOT NULL DEFAULT 'running'
                          CHECK (status IN ('running', 'finished', 'interrupted')),
    integrity_verdict TEXT CHECK (integrity_verdict IS NULL
                          OR integrity_verdict IN ('PASS', 'FAIL', 'UNJUDGED')),
    provenance_json   TEXT,
    metadata_json     TEXT,
    UNIQUE (project_id, external_id)
);

CREATE INDEX runs_started_at ON runs (started_at);
CREATE INDEX runs_lane_branch ON runs (project_id, lane, git_branch, started_at);

CREATE TABLE invocations (
    id           INTEGER PRIMARY KEY,
    project_id   TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    run_id       TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    exit_status  INTEGER,
    argv_json    TEXT,
    worker_count INTEGER,
    lane         TEXT,
    UNIQUE (run_id, seq)
);

CREATE TABLE cases (
    id                  INTEGER PRIMARY KEY,
    project_id          TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    case_id             TEXT NOT NULL,
    case_key            TEXT NOT NULL,
    framework           TEXT NOT NULL DEFAULT 'pytest',
    title               TEXT,
    module              TEXT,
    qualname            TEXT,
    file_path           TEXT,
    line                INTEGER,
    description         TEXT,
    owner               TEXT,
    priority            TEXT,
    markers_json        TEXT,
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
    first_seen_at       TEXT,
    last_seen_at        TEXT,
    current_revision_id TEXT,
    UNIQUE (project_id, case_id),
    UNIQUE (project_id, case_key)
);

CREATE TABLE case_revisions (
    id                INTEGER PRIMARY KEY,
    project_id        TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    revision_id       TEXT NOT NULL,
    case_id           TEXT NOT NULL,
    source_hash       TEXT,
    file_blob_sha     TEXT,
    git_sha           TEXT,
    steps_json        TEXT,
    first_seen_run_id TEXT,
    created_at        TEXT,
    UNIQUE (project_id, case_id, revision_id),
    FOREIGN KEY (project_id, case_id) REFERENCES cases (project_id, case_id) ON DELETE CASCADE
);

CREATE TABLE test_results (
    id                  INTEGER PRIMARY KEY,
    project_id          TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    run_id              TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    invocation_id       INTEGER REFERENCES invocations (id) ON DELETE SET NULL,
    case_id             TEXT,
    revision_id         TEXT,
    nodeid              TEXT NOT NULL,
    param_id            TEXT,
    attempt             INTEGER NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    outcome             TEXT NOT NULL CHECK (outcome IN (
                            'passed', 'failed', 'error', 'xfailed', 'skipped', 'not_run')),
    is_cleared          INTEGER NOT NULL DEFAULT 0 CHECK (is_cleared IN (0, 1)),
    duration_ms         INTEGER,
    setup_ms            INTEGER,
    call_ms             INTEGER,
    teardown_ms         INTEGER,
    started_at          TEXT,
    finished_at         TEXT,
    worker              TEXT,
    browser             TEXT,
    shard_index         INTEGER,
    message             TEXT,
    longrepr            TEXT,
    failure_fingerprint TEXT,
    skip_reason         TEXT,
    defect_ref          TEXT,
    markers_json        TEXT,
    UNIQUE (run_id, nodeid, attempt),
    FOREIGN KEY (project_id, case_id) REFERENCES cases (project_id, case_id),
    FOREIGN KEY (project_id, case_id, revision_id)
        REFERENCES case_revisions (project_id, case_id, revision_id)
);

CREATE INDEX test_results_case_run ON test_results (case_id, run_id);
CREATE INDEX test_results_outcome ON test_results (outcome);

-- The final attempt of every test in a run: the same rule the run-trust gate applies.
CREATE VIEW final_results AS
SELECT tr.*
FROM test_results AS tr
WHERE tr.attempt = (
    SELECT MAX(t2.attempt) FROM test_results AS t2
    WHERE t2.run_id = tr.run_id AND t2.nodeid = tr.nodeid
);

CREATE TABLE evidence (
    id         INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    run_id     TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    result_id  INTEGER REFERENCES test_results (id) ON DELETE CASCADE,
    nodeid     TEXT NOT NULL DEFAULT '',
    attempt    INTEGER NOT NULL DEFAULT 0,
    kind       TEXT NOT NULL CHECK (kind IN (
                   'trace', 'video', 'screenshot', 'failure_bundle', 'har', 'log',
                   'allure', 'report', 'other')),
    uri        TEXT NOT NULL,
    sha256     TEXT,
    bytes      INTEGER,
    created_at TEXT,
    UNIQUE (run_id, nodeid, attempt, kind, uri)
);

CREATE INDEX evidence_result ON evidence (result_id);

-- Mirror of verification/verdicts.py rows. NULL ticket/subject are stored as '' so the
-- natural key below is a real UNIQUE (SQLite treats NULLs as distinct).
CREATE TABLE verdicts (
    id          INTEGER PRIMARY KEY,
    project_id  TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    run_id      TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    nodeid      TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('expected_red', 'coverage')),
    ticket      TEXT NOT NULL DEFAULT '',
    verdict     TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    subject     TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL DEFAULT '',
    UNIQUE (run_id, nodeid, kind, ticket, verdict, detail, subject, recorded_at)
);

CREATE INDEX verdicts_run ON verdicts (run_id, kind);

-- Mirror of gate/provenance.py data-origin rows (fresh vs reused test data).
CREATE TABLE data_origin (
    id              INTEGER PRIMARY KEY,
    project_id      TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    run_id          TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    nodeid          TEXT NOT NULL,
    origin          TEXT NOT NULL,
    fixture         TEXT NOT NULL DEFAULT '',
    entity_id       TEXT NOT NULL DEFAULT '',
    candidates      INTEGER NOT NULL DEFAULT 1,
    attributes_json TEXT,
    recorded_at     TEXT NOT NULL DEFAULT '',
    UNIQUE (run_id, nodeid, origin, fixture, entity_id, recorded_at)
);

-- Generic link table: requirement, defect, feature, doc and tracker ids (kind 'tms:<name>').
CREATE TABLE case_links (
    id         INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    case_id    TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (
                   kind IN ('requirement', 'defect', 'feature', 'doc', 'review')
                   OR kind LIKE 'tms:%'),
    ref        TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('marker', 'import', 'manual')),
    created_at TEXT,
    UNIQUE (project_id, case_id, kind, ref),
    FOREIGN KEY (project_id, case_id) REFERENCES cases (project_id, case_id) ON DELETE CASCADE
);

CREATE INDEX case_links_ref ON case_links (kind, ref);
