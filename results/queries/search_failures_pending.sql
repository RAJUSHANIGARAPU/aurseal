-- Failed final results of runs not indexed yet (every run when :full is 1), oldest first.
SELECT f.run_id, f.nodeid, f.case_id, f.outcome, f.message, f.failure_fingerprint,
       f.project_id, r.started_at AS run_started_at, c.title, c.owner, c.markers_json
FROM final_results f
JOIN runs r ON r.run_id = f.run_id
LEFT JOIN cases c ON c.project_id = f.project_id AND c.case_id = f.case_id
WHERE f.outcome IN ('failed', 'error')
  AND (COALESCE(:full, 0) = 1 OR f.run_id NOT IN (SELECT run_id FROM search_runs))
ORDER BY r.started_at, r.id, f.nodeid
