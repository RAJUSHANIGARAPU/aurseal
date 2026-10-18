-- One manual execution.
SELECT m.* FROM qap_manual_executions m WHERE m.project_id = :project_id AND m.id = :id
