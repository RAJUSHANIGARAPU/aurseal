-- trace 0004: coverage-gap reports per run (the CI step's output, kept for history).

CREATE TABLE trace_gap_reports (
    id             INTEGER PRIMARY KEY,
    project_id     TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    run_id         TEXT,
    branch         TEXT,
    generated_at   TEXT NOT NULL,
    mode           TEXT NOT NULL CHECK (mode IN ('static', 'runtime')),
    sources_json   TEXT NOT NULL,
    gap_count      INTEGER NOT NULL,
    new_gap_count  INTEGER NOT NULL,
    baseline_hash  TEXT,
    body_json      TEXT NOT NULL
);

CREATE INDEX trace_gap_reports_branch ON trace_gap_reports (project_id, branch, id);
