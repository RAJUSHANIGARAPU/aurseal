-- Runs of one project, newest first; every other filter is optional.
SELECT r.*,
    (SELECT COUNT(*) FROM final_results f WHERE f.run_id = r.run_id) AS result_count
FROM runs r
WHERE r.project_id = :project_id
  AND (:status IS NULL OR r.status = :status)
  AND (:branch IS NULL OR r.git_branch = :branch)
  AND (:source IS NULL OR r.source = :source)
  AND (:since IS NULL OR r.started_at >= :since)
ORDER BY r.started_at DESC, r.id DESC
LIMIT :limit OFFSET :offset
