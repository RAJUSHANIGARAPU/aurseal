-- requires: core>=1
-- trace 0001: the requirements registry as the store sees it. Links from cases to
-- requirements live in the core `case_links` table (kind 'requirement').

CREATE TABLE trace_requirements (
    id             INTEGER PRIMARY KEY,
    project_id     TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    requirement_id TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    priority       TEXT,
    source         TEXT NOT NULL DEFAULT 'local',
    source_ref     TEXT,
    url            TEXT,
    labels_json    TEXT,
    release        TEXT,
    parent_id      TEXT,
    content_hash   TEXT,
    synced_at      TEXT,
    UNIQUE (project_id, requirement_id)
);
