-- core 0002 (variant without FTS5): the same columns as a plain table; search uses LIKE.
CREATE TABLE search_docs (
    title       TEXT,
    body        TEXT,
    doc_type    TEXT,
    doc_ref     TEXT,
    doc_key     TEXT,
    project_id  TEXT NOT NULL DEFAULT 'default',
    outcome     TEXT,
    markers     TEXT,
    owner       TEXT,
    recorded_at TEXT,
    href        TEXT
);

CREATE INDEX search_docs_type ON search_docs (doc_type, doc_key);

CREATE TABLE search_meta (
    project_id          TEXT PRIMARY KEY DEFAULT 'default' REFERENCES projects (id),
    last_indexed_run_id TEXT,
    last_indexed_at     TEXT,
    indexed_at          TEXT
);

-- Runs whose failures are in the index, so an incremental reindex picks up exactly the
-- runs it has not seen (a bundle imported late, with an older start, included).
CREATE TABLE search_runs (
    project_id TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    run_id     TEXT PRIMARY KEY REFERENCES runs (run_id) ON DELETE CASCADE,
    indexed_at TEXT NOT NULL
);
