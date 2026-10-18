-- The final attempt of every test in a run (optionally one outcome).
SELECT f.*, inv.seq AS invocation_seq,
    (SELECT COUNT(*) FROM test_results t2
     WHERE t2.run_id = f.run_id AND t2.nodeid = f.nodeid) AS attempts
FROM final_results f
LEFT JOIN invocations inv ON inv.id = f.invocation_id
WHERE f.run_id = :run_id
  AND (:outcome IS NULL OR f.outcome = :outcome)
ORDER BY f.nodeid
