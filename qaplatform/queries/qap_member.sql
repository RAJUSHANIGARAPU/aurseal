-- One user's binding in one project.
SELECT b.user_id, b.role FROM qap_role_bindings b
WHERE b.project_id = :project_id AND b.user_id = :user_id
