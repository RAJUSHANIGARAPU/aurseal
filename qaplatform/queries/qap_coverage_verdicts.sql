-- Coverage verdict rows of one project, oldest run first.
SELECT v.nodeid, v.subject, v.verdict, v.run_id, r.source, v.recorded_at
FROM verdicts v
JOIN runs r ON r.run_id = v.run_id
WHERE v.project_id = :project_id AND v.kind = 'coverage'
  AND (:release IS NULL OR r.release_label = :release)
ORDER BY r.started_at, r.id, v.id
