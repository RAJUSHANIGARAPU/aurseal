-- Evidence links of one run (run-level rows have an empty nodeid).
SELECT * FROM evidence WHERE run_id = :run_id ORDER BY nodeid, attempt, kind, uri
