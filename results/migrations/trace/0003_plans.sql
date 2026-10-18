-- trace 0003: test plans by git blob revision, and their evaluations against runs.

CREATE TABLE trace_plans (
    id          INTEGER PRIMARY KEY,
    project_id  TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    plan_id     TEXT NOT NULL,
    revision    TEXT NOT NULL,
    release     TEXT,
    body_json   TEXT NOT NULL,
    stored_at   TEXT NOT NULL,
    UNIQUE (project_id, plan_id, revision)
);

CREATE TABLE trace_plan_evaluations (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    plan_id       TEXT NOT NULL,
    plan_revision TEXT NOT NULL,
    evaluated_at  TEXT NOT NULL,
    run_ids_json  TEXT NOT NULL,
    result        TEXT NOT NULL CHECK (result IN ('met', 'unmet', 'no_evidence')),
    criteria_json TEXT NOT NULL
);

CREATE INDEX trace_plan_evaluations_plan ON trace_plan_evaluations (project_id, plan_id, id);
