-- Step results of one manual execution (scoped through the execution's project).
SELECT s.* FROM qap_manual_step_results s
JOIN qap_manual_executions m ON m.id = s.execution_id
WHERE m.project_id = :project_id AND s.execution_id = :id
ORDER BY s.step_no
