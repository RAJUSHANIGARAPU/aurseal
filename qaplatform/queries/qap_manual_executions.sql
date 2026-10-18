-- Manual executions of one run.
SELECT m.* FROM qap_manual_executions m
WHERE m.project_id = :project_id AND m.run_id = :run_id
ORDER BY m.case_key
