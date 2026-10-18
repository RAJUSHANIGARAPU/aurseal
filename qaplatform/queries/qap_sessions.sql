-- Exploratory sessions of one project, newest first (optionally those covering a requirement).
SELECT s.* FROM qap_sessions s
WHERE s.project_id = :project_id
  AND (:requirement IS NULL OR EXISTS (
        SELECT 1 FROM json_each(s.requirements_json) j WHERE j.value = :requirement))
ORDER BY s.started_at DESC
LIMIT :limit
