-- One exploratory session.
SELECT s.* FROM qap_sessions s WHERE s.project_id = :project_id AND s.id = :id
