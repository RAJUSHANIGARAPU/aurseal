-- Evidence links of one run.
SELECT e.* FROM evidence e
WHERE e.project_id = :project_id AND e.run_id = :run_id
ORDER BY e.nodeid, e.attempt, e.kind, e.uri
