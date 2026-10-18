-- trace 0007: one row per requirements sync, with its diff and orphaned links.

CREATE TABLE trace_requirement_syncs (
    id                  INTEGER PRIMARY KEY,
    project_id          TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    sources_json        TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    added               INTEGER NOT NULL DEFAULT 0,
    changed             INTEGER NOT NULL DEFAULT 0,
    retired             INTEGER NOT NULL DEFAULT 0,
    orphaned_links_json TEXT NOT NULL DEFAULT '[]',
    error               TEXT,
    body_json           TEXT NOT NULL
);
