-- requires: qaplatform>=4
-- qaplatform 0005: exploratory sessions (charter, timestamped notes, debrief). A session is
-- a run of source 'exploratory'; what it contributes to traceability is 'recorded', never
-- 'proved'.

CREATE TABLE qap_sessions (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects (id),
    run_id            TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    charter           TEXT NOT NULL,
    area              TEXT NOT NULL DEFAULT '',
    requirements_json TEXT NOT NULL DEFAULT '[]',
    timebox_min       INTEGER CHECK (timebox_min IS NULL OR timebox_min > 0),
    tester_user_id    TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'ended', 'abandoned')),
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    debrief           TEXT
);

CREATE INDEX qap_sessions_project ON qap_sessions (project_id, started_at);

CREATE TABLE qap_session_notes (
    session_id     TEXT NOT NULL REFERENCES qap_sessions (id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    ts             TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('observation', 'question', 'bug', 'idea')),
    text           TEXT NOT NULL,
    attachment_uri TEXT,
    defect_key     TEXT,
    author         TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
