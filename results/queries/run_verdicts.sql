-- Verdict rows of one run (optionally one kind).
SELECT * FROM verdicts
WHERE run_id = :run_id AND (:kind IS NULL OR kind = :kind)
ORDER BY nodeid, recorded_at, id
