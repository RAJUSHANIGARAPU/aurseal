-- Final results of the last :window runs (unsound runs only when asked), oldest first.
WITH win AS (
    SELECT run_id FROM runs
    WHERE project_id = COALESCE(:project_id, 'default')
      AND (:lane IS NULL OR lane = :lane)
      AND (COALESCE(:include_unsound, 0) = 1 OR integrity_verdict IS NULL
           OR integrity_verdict <> 'FAIL')
    ORDER BY started_at DESC, id DESC
    LIMIT COALESCE(:window, 30)
)
SELECT f.run_id, f.nodeid, f.case_id, f.outcome, f.attempt, f.duration_ms,
       f.failure_fingerprint, f.message,
       r.started_at AS run_started_at, c.title, c.case_key,
       (SELECT COUNT(*) FROM test_results t2
        WHERE t2.run_id = f.run_id AND t2.nodeid = f.nodeid
          AND t2.attempt < f.attempt AND t2.outcome IN ('failed', 'error')) AS failed_attempts
FROM final_results f
JOIN win ON win.run_id = f.run_id
JOIN runs r ON r.run_id = f.run_id
LEFT JOIN cases c ON c.project_id = f.project_id AND c.case_id = f.case_id
ORDER BY r.started_at, r.id, f.nodeid
