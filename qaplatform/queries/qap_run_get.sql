-- One run of one project.
SELECT r.* FROM runs r WHERE r.project_id = :project_id AND r.run_id = :run_id
