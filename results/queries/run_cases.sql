-- Cases referenced by a run's results.
SELECT c.* FROM cases c
WHERE EXISTS (
    SELECT 1 FROM test_results tr
    WHERE tr.run_id = :run_id AND tr.case_id = c.case_id AND tr.project_id = c.project_id
)
ORDER BY c.case_key
