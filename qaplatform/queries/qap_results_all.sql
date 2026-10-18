-- Every attempt of every test of one run.
SELECT t.* FROM test_results t
WHERE t.project_id = :project_id AND t.run_id = :run_id
  AND (:outcome IS NULL OR t.outcome = :outcome)
ORDER BY t.nodeid, t.attempt
LIMIT :limit OFFSET :offset
