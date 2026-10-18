-- The newest audit row of one project.
SELECT a.seq, a.row_hash FROM qap_audit_log a WHERE a.project_id = :project_id
ORDER BY a.seq DESC LIMIT 1
