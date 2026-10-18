-- Median call duration per node id over executed attempts (feeds gate.shard planning).
WITH d AS (
    SELECT nodeid, duration_ms,
        ROW_NUMBER() OVER (PARTITION BY nodeid ORDER BY duration_ms) AS rn,
        COUNT(*) OVER (PARTITION BY nodeid) AS n
    FROM test_results
    WHERE duration_ms IS NOT NULL
      AND outcome IN ('passed', 'failed', 'error')
      AND (:project_id IS NULL OR project_id = :project_id)
)
SELECT nodeid, AVG(duration_ms) AS duration_ms
FROM d
WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
GROUP BY nodeid
ORDER BY nodeid
