-- Every attempt of the last :window runs, in execution order, with run context.
WITH win AS (
    SELECT run_id FROM runs
    WHERE project_id = COALESCE(:project_id, 'default')
    ORDER BY started_at DESC, id DESC
    LIMIT COALESCE(:window, 30)
)
SELECT tr.run_id, tr.nodeid, tr.attempt, tr.outcome, tr.duration_ms, tr.message,
       tr.started_at, tr.worker, r.environment, r.started_at AS run_started_at,
       (SELECT MAX(t2.attempt) FROM test_results t2
        WHERE t2.run_id = tr.run_id AND t2.nodeid = tr.nodeid) AS final_attempt
FROM test_results tr
JOIN win ON win.run_id = tr.run_id
JOIN runs r ON r.run_id = tr.run_id
ORDER BY r.started_at, r.id, tr.nodeid, tr.attempt
