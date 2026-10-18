-- trace 0006: defect states as last read from the trackers. Links from cases to defects
-- live in the core `case_links` table (kind 'defect').

CREATE TABLE trace_defects (
    id          INTEGER PRIMARY KEY,
    project_id  TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    defect_id   TEXT NOT NULL,
    tracker     TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL CHECK (state IN ('open', 'closed', 'unknown')),
    severity    TEXT,
    labels_json TEXT,
    url         TEXT,
    fetched_at  TEXT NOT NULL,
    UNIQUE (project_id, defect_id)
);
