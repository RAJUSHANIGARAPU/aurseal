-- Final results of one case in every run of one project, oldest run first.
SELECT f.run_id, f.nodeid, f.outcome, f.attempt, f.duration_ms, f.message, f.started_at,
       r.started_at AS run_started_at, r.integrity_verdict, r.git_sha, r.git_branch, r.source,
       r.release_label,
       (SELECT COUNT(*) FROM test_results t2
        WHERE t2.run_id = f.run_id AND t2.nodeid = f.nodeid
          AND t2.attempt < f.attempt AND t2.outcome IN ('failed', 'error')) AS failed_attempts
FROM final_results f
JOIN runs r ON r.run_id = f.run_id
WHERE f.project_id = :project_id AND f.case_id = :case_id
ORDER BY r.started_at, r.id, f.nodeid
LIMIT :limit
