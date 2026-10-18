-- One run request of one project.
SELECT q.* FROM qap_run_requests q WHERE q.project_id = :project_id AND q.id = :id
