-- The run a client's own id maps to, within one project.
SELECT r.* FROM runs r WHERE r.project_id = :project_id AND r.external_id = :external_id
