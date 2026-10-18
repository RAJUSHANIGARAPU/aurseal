-- Final results of one project's cases, oldest run first (the caller keeps the last per case).
SELECT f.case_id, f.nodeid, f.outcome, f.run_id, r.source, r.started_at
FROM final_results f
JOIN runs r ON r.run_id = f.run_id
WHERE f.project_id = :project_id AND f.case_id IS NOT NULL
  AND (:release IS NULL OR r.release_label = :release)
ORDER BY r.started_at, r.id
