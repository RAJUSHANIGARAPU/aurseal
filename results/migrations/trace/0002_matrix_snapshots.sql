-- trace 0002: matrix snapshots, so a release verdict can point at the exact matrix it used.

CREATE TABLE trace_matrix_snapshots (
    id           INTEGER PRIMARY KEY,
    project_id   TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    scope_key    TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    run_ids_json TEXT NOT NULL,
    body_json    TEXT NOT NULL
);

CREATE INDEX trace_matrix_snapshots_scope ON trace_matrix_snapshots (project_id, scope_key);
