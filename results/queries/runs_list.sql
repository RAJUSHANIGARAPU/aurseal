-- Runs, newest first; every filter is optional.
SELECT r.*,
    (SELECT COUNT(*) FROM final_results f WHERE f.run_id = r.run_id) AS result_count
FROM runs r
WHERE (:project_id IS NULL OR r.project_id = :project_id)
  AND (:lane IS NULL OR r.lane = :lane)
  AND (:branch IS NULL OR r.git_branch = :branch)
  AND (:status IS NULL OR r.status = :status)
  AND (:since IS NULL OR r.started_at >= :since)
ORDER BY r.started_at DESC, r.id DESC
LIMIT COALESCE(:limit, 100) OFFSET COALESCE(:offset, 0)
