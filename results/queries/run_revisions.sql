-- Revisions referenced by a run's results.
SELECT cr.* FROM case_revisions cr
WHERE EXISTS (
    SELECT 1 FROM test_results tr
    WHERE tr.run_id = :run_id AND tr.case_id = cr.case_id
      AND tr.revision_id = cr.revision_id AND tr.project_id = cr.project_id
)
ORDER BY cr.case_id, cr.created_at
