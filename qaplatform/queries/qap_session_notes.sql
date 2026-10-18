-- Notes of one session, in order (scoped through the session's project).
SELECT n.* FROM qap_session_notes n
JOIN qap_sessions s ON s.id = n.session_id
WHERE s.project_id = :project_id AND n.session_id = :id
ORDER BY n.seq
