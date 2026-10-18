-- Verdict rows of one run.
SELECT v.* FROM verdicts v
WHERE v.project_id = :project_id AND v.run_id = :run_id
ORDER BY v.id
