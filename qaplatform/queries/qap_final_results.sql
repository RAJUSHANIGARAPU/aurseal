-- The final attempt of every test of one run, with how many attempts it took and how many
-- earlier attempts were red (a retry does not erase a flake).
SELECT f.*,
    (SELECT COUNT(*) FROM test_results t2
     WHERE t2.run_id = f.run_id AND t2.nodeid = f.nodeid) AS attempts,
    (SELECT COUNT(*) FROM test_results t3
     WHERE t3.run_id = f.run_id AND t3.nodeid = f.nodeid AND t3.attempt < f.attempt
       AND t3.outcome IN ('failed', 'error')) AS failed_attempts
FROM final_results f
WHERE f.project_id = :project_id AND f.run_id = :run_id
  AND (:outcome IS NULL OR f.outcome = :outcome)
ORDER BY f.nodeid
LIMIT :limit OFFSET :offset
