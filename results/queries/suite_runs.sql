-- Per-run totals over final results, newest first; lane/branch optional.
SELECT r.run_id, r.lane, r.git_branch, r.git_sha, r.started_at, r.finished_at, r.status,
       r.integrity_verdict, r.expected_count, r.release_label,
       COUNT(f.id) AS selected,
       COALESCE(SUM(f.outcome = 'passed'), 0) AS passed,
       COALESCE(SUM(f.outcome = 'failed'), 0) AS failed,
       COALESCE(SUM(f.outcome = 'error'), 0) AS error,
       COALESCE(SUM(f.outcome = 'xfailed'), 0) AS xfailed,
       COALESCE(SUM(f.outcome = 'skipped'), 0) AS skipped,
       COALESCE(SUM(f.outcome = 'not_run'), 0) AS not_run,
       COALESCE(SUM(f.outcome = 'passed' AND f.attempt > 1), 0) AS passed_on_retry,
       COALESCE(SUM(f.duration_ms), 0) AS test_time_ms
FROM runs r
LEFT JOIN final_results f ON f.run_id = r.run_id
WHERE r.project_id = COALESCE(:project_id, 'default')
  AND (:lane IS NULL OR r.lane = :lane)
  AND (:branch IS NULL OR r.git_branch = :branch)
GROUP BY r.id
ORDER BY r.started_at DESC, r.id DESC
LIMIT COALESCE(:limit, 30)
