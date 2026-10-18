-- requires: qaplatform>=3
-- qaplatform 0004: manual executions. Each execution pins the case revision it followed,
-- so a later edit of the case never rewrites what a tester actually did.

CREATE TABLE qap_manual_executions (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects (id),
    run_id            TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    case_id           TEXT NOT NULL,
    case_key          TEXT NOT NULL,
    revision_id       TEXT NOT NULL,
    title             TEXT NOT NULL DEFAULT '',
    steps_json        TEXT NOT NULL,
    expected_json     TEXT NOT NULL DEFAULT '[]',
    requirements_json TEXT NOT NULL DEFAULT '[]',
    tester_user_id    TEXT,
    status            TEXT NOT NULL DEFAULT 'in_progress' CHECK (status IN (
                          'in_progress', 'passed', 'failed', 'blocked', 'skipped')),
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    UNIQUE (run_id, case_key)
);

CREATE INDEX qap_manual_executions_run ON qap_manual_executions (project_id, run_id);

CREATE TABLE qap_manual_step_results (
    execution_id TEXT NOT NULL REFERENCES qap_manual_executions (id) ON DELETE CASCADE,
    step_no      INTEGER NOT NULL CHECK (step_no >= 1),
    status       TEXT NOT NULL CHECK (status IN ('pass', 'fail', 'blocked', 'na')),
    actual       TEXT NOT NULL DEFAULT '',
    evidence_uri TEXT,
    recorded_by  TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    PRIMARY KEY (execution_id, step_no)
);
