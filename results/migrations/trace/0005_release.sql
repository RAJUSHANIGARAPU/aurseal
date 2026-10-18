-- trace 0005: release candidates (with the runs they were tested by) and their verdicts.
-- Waivers stay file-only (committed by pull request) until the platform has writer roles.

CREATE TABLE trace_release_candidates (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    release_id    TEXT NOT NULL,
    candidate_id  TEXT NOT NULL,
    version       TEXT,
    commit_sha    TEXT,
    plan_id       TEXT,
    plan_revision TEXT,
    run_ids_json  TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL,
    UNIQUE (project_id, release_id, candidate_id)
);

CREATE TABLE trace_release_verdicts (
    id                 INTEGER PRIMARY KEY,
    project_id         TEXT NOT NULL DEFAULT 'default' REFERENCES projects (id),
    release_id         TEXT NOT NULL,
    candidate_id       TEXT NOT NULL,
    computed_at        TEXT NOT NULL,
    verdict            TEXT NOT NULL
                           CHECK (verdict IN ('GO', 'GO_WITH_WAIVERS', 'NO_GO', 'NO_VERDICT')),
    inputs_hash        TEXT NOT NULL,
    policy_revision    TEXT,
    reasons_json       TEXT NOT NULL,
    matrix_snapshot_id INTEGER REFERENCES trace_matrix_snapshots (id),
    body_json          TEXT NOT NULL
);

CREATE INDEX trace_release_verdicts_candidate
    ON trace_release_verdicts (project_id, release_id, candidate_id, id);
