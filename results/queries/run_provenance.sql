-- Data-origin rows of one run.
SELECT * FROM data_origin WHERE run_id = :run_id ORDER BY nodeid, recorded_at, id
