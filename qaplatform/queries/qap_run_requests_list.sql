-- Run requests of one project, newest first.
SELECT q.* FROM qap_run_requests q WHERE q.project_id = :project_id
ORDER BY q.created_at DESC LIMIT :limit
