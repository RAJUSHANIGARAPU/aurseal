-- Audit rows of one project, keyset-paginated on seq; every filter is optional.
SELECT a.* FROM qap_audit_log a
WHERE a.project_id = :project_id
  AND (:action IS NULL OR a.action = :action)
  AND (:actor IS NULL OR a.actor_user_id = :actor)
  AND (:since IS NULL OR a.ts >= :since)
  AND a.seq > :after
ORDER BY a.seq
LIMIT :limit
