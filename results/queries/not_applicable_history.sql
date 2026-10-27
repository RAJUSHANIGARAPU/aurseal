-- Every final result of a test that was ever reported not applicable, newest run first,
-- with whether that run recorded it as not applicable (results.scope reads this).
SELECT f.nodeid, f.run_id, r.started_at, f.outcome,
    EXISTS (
        SELECT 1 FROM verdicts v
        WHERE v.run_id = f.run_id AND v.nodeid = f.nodeid
          AND v.kind = 'coverage' AND v.verdict = 'not_applicable'
    ) AS not_applicable
FROM final_results f
JOIN runs r ON r.run_id = f.run_id
WHERE f.nodeid IN (
    SELECT DISTINCT nodeid FROM verdicts
    WHERE kind = 'coverage' AND verdict = 'not_applicable'
)
ORDER BY f.nodeid, r.started_at DESC, r.id DESC
