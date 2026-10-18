-- Role bindings of one project, with the user's name and state.
SELECT b.user_id, b.role, b.granted_by, b.granted_at, u.display_name, u.disabled
FROM qap_role_bindings b JOIN qap_users u ON u.id = b.user_id
WHERE b.project_id = :project_id
ORDER BY b.user_id
