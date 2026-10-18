-- Final results of one case in every run, oldest run first, with run context.
SELECT f.run_id, f.nodeid, f.outcome, f.attempt, f.duration_ms, f.revision_id,
       f.failure_fingerprint, f.message, f.started_at,
       r.started_at AS run_started_at, r.integrity_verdict, r.git_sha, r.lane, r.git_branch,
       (SELECT COUNT(*) FROM test_results t2
        WHERE t2.run_id = f.run_id AND t2.nodeid = f.nodeid
          AND t2.attempt < f.attempt AND t2.outcome IN ('failed', 'error')) AS failed_attempts
FROM final_results f
JOIN runs r ON r.run_id = f.run_id
WHERE f.case_id = :case_id AND f.project_id = COALESCE(:project_id, 'default')
ORDER BY r.started_at, r.id, f.nodeid
