-- Every stored attempt of a run (optionally one outcome), with its invocation number.
SELECT tr.*, inv.seq AS invocation_seq
FROM test_results tr
LEFT JOIN invocations inv ON inv.id = tr.invocation_id
WHERE tr.run_id = :run_id
  AND (:outcome IS NULL OR tr.outcome = :outcome)
ORDER BY tr.nodeid, tr.attempt
