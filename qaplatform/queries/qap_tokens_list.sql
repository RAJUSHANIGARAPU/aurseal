-- Tokens of one project; never the hash.
SELECT t.id, t.user_id, t.role, t.label, t.created_at, t.expires_at, t.last_used_at,
       t.revoked_at
FROM qap_tokens t WHERE t.project_id = :project_id ORDER BY t.created_at
