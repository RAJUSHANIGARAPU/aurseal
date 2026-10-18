-- core 0002: full-text search index (SQLite FTS5). A build without FTS5 runs
-- 0002_search.nofts.sql instead: same columns, a plain table, LIKE search.
CREATE VIRTUAL TABLE search_docs USING fts5(
    title,
    body,
    doc_type UNINDEXED,
    doc_ref UNINDEXED,
    doc_key UNINDEXED,
    project_id UNINDEXED,
    outcome UNINDEXED,
    markers UNINDEXED,
    owner UNINDEXED,
    recorded_at UNINDEXED,
    href UNINDEXED,
    tokenize = 'porter unicode61'
);

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
