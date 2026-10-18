-- requires: qaplatform>=2
-- qaplatform 0003: users and per-project role bindings (identity is API tokens only).
-- A token's effective role is the lower of its own role and its owner's binding, checked
-- on every request, so removing a member or lowering a role takes effect immediately.

CREATE TABLE qap_users (
    id                TEXT PRIMARY KEY,
    display_name      TEXT NOT NULL DEFAULT '',
    is_instance_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_instance_admin IN (0, 1)),
    disabled          INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1)),
    created_at        TEXT NOT NULL
);

CREATE TABLE qap_role_bindings (
    user_id    TEXT NOT NULL REFERENCES qap_users (id),
    project_id TEXT NOT NULL REFERENCES projects (id),
    role       TEXT NOT NULL CHECK (role IN ('viewer', 'tester', 'lead', 'admin')),
    granted_by TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    PRIMARY KEY (user_id, project_id)
);

-- Tokens issued before this migration keep working: their owners become users, instance
-- token owners become instance admins, and project tokens bind their owner at the token's role.
INSERT INTO qap_users (id, display_name, is_instance_admin, created_at)
SELECT user_id, user_id, MAX(CASE WHEN project_id IS NULL THEN 1 ELSE 0 END), MIN(created_at)
FROM qap_tokens GROUP BY user_id;

INSERT INTO qap_role_bindings (user_id, project_id, role, granted_by, granted_at)
SELECT user_id, project_id,
       CASE MAX(CASE role WHEN 'admin' THEN 4 WHEN 'lead' THEN 3 WHEN 'tester' THEN 2 ELSE 1 END)
           WHEN 4 THEN 'admin' WHEN 3 THEN 'lead' WHEN 2 THEN 'tester' ELSE 'viewer' END,
       'migration', MIN(created_at)
FROM qap_tokens WHERE project_id IS NOT NULL GROUP BY user_id, project_id;
