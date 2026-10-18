-- Verdict rows of one project, keyset-paginated on the row id.
SELECT v.* FROM verdicts v
WHERE v.project_id = :project_id
  AND (:run_id IS NULL OR v.run_id = :run_id)
  AND (:ticket IS NULL OR v.ticket = :ticket)
  AND (:kind IS NULL OR v.kind = :kind)
  AND v.id > :after
ORDER BY v.id
LIMIT :limit
