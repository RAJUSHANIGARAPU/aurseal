-- The whole audit chain of one project, in order.
SELECT a.* FROM qap_audit_log a WHERE a.project_id = :project_id ORDER BY a.seq
